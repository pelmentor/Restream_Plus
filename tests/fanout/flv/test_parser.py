"""Tests for `app.fanout.flv.parser`.

The byte layouts here are anchored to OBS source (Rule №7):

- `reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501` — video multitrack.
- `reference/obs-studio/plugins/obs-outputs/flv-mux.c:388-432` — audio multitrack.
- `reference/obs-studio/plugins/obs-outputs/flv-mux.c:51` — `FRAME_HEADER_EX = 0x80`.
- `reference/obs-studio/plugins/obs-outputs/flv-mux.c:43` — `AUDIO_HEADER_EX = 0x90`.

Synthetic fixtures emit byte-for-byte what `flv_packet_ex` produces
with `idx > 0`, so the tests pin the parser against the OBS wire
format rather than against the parser's own assumptions.
"""

from __future__ import annotations

import io
import json
import struct
from collections.abc import Iterable
from dataclasses import dataclass

import pytest
from app.fanout.flv.parser import (
    AUDIO_ERTMP_HIGH_NIBBLE,
    AUDIO_PACKETTYPE_MULTITRACK,
    MULTITRACK_TYPE_MANY_TRACKS,
    MULTITRACK_TYPE_ONE_TRACK,
    VIDEO_ERTMP_MARKER,
    VIDEO_PACKET_TYPE_MASK,
    VIDEO_PACKETTYPE_MULTITRACK,
    AudioTagInfo,
    FlvParseError,
    ScriptTagInfo,
    VideoTagInfo,
    analyse,
    parse_flv,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Synthetic FLV builders — public for both unit + property tests


def _u24_be(value: int) -> bytes:
    return struct.pack(">I", value)[1:]


def flv_header(*, audio: bool = True, video: bool = True) -> bytes:
    flags = 0
    if audio:
        flags |= 0x04
    if video:
        flags |= 0x01
    return b"FLV" + bytes([1, flags]) + struct.pack(">I", 9) + b"\x00\x00\x00\x00"


def flv_tag(tag_type: int, body: bytes, timestamp_ms: int = 0) -> bytes:
    data_size = len(body)
    ts_lower = timestamp_ms & 0xFFFFFF
    ts_ext = (timestamp_ms >> 24) & 0xFF
    header = (
        bytes([tag_type]) + _u24_be(data_size) + _u24_be(ts_lower) + bytes([ts_ext]) + _u24_be(0)
    )
    prev = struct.pack(">I", 11 + data_size)
    return header + body + prev


def video_legacy_body() -> bytes:
    # frame_type=KEY (1<<4=0x10) | codec_id=H.264 (7)
    return bytes([0x17]) + b"\x00\x00\x00\x00\x00fake-h264-payload"


def video_ertmp_seqstart_body(fourcc: bytes = b"avc1") -> bytes:
    # byte0: 0x80 (E-RTMP) | 0x10 (KEY) | 0x00 (SEQ_START) = 0x90
    return bytes([0x90]) + fourcc + b"fake-seq-start-payload"


def video_multitrack_one_track_body(
    *, fourcc: bytes, track_id: int, inner_packet_type: int = 1
) -> bytes:
    # byte0: 0x80 (E-RTMP) | 0x20 (INTER) | 0x06 (MULTITRACK) = 0xA6
    byte0 = VIDEO_ERTMP_MARKER | 0x20 | VIDEO_PACKETTYPE_MULTITRACK
    # byte1: MULTITRACK_TYPE_ONE_TRACK (0x00) | inner_packet_type
    byte1 = MULTITRACK_TYPE_ONE_TRACK | inner_packet_type
    return bytes([byte0, byte1]) + fourcc + bytes([track_id]) + b"fake-payload"


def video_multitrack_many_tracks_body() -> bytes:
    byte0 = VIDEO_ERTMP_MARKER | 0x20 | VIDEO_PACKETTYPE_MULTITRACK
    byte1 = MULTITRACK_TYPE_MANY_TRACKS | 0x01
    return bytes([byte0, byte1]) + b"opaque-many-tracks-data"


def audio_legacy_body() -> bytes:
    # sound_format=10 (AAC, 0xA0) | sound_rate=3 (44.1kHz, 0x0C) |
    # size=1 (16-bit, 0x02) | type=1 (stereo, 0x01)
    return bytes([0xAF, 0x01]) + b"fake-aac-payload"


def audio_ertmp_seqstart_body(fourcc: bytes = b"mp4a") -> bytes:
    return bytes([AUDIO_ERTMP_HIGH_NIBBLE]) + fourcc + b"fake-aac-seq-start"


def audio_multitrack_one_track_body(*, fourcc: bytes, track_id: int) -> bytes:
    byte0 = AUDIO_ERTMP_HIGH_NIBBLE | AUDIO_PACKETTYPE_MULTITRACK
    byte1 = MULTITRACK_TYPE_ONE_TRACK | 0x01
    return bytes([byte0, byte1]) + fourcc + bytes([track_id]) + b"audio-frame-payload"


def script_body() -> bytes:
    return b"\x02\x00\x0aonMetaData\x05"


def make_flv(tags: Iterable[bytes]) -> bytes:
    return flv_header() + b"".join(tags)


# ---------------------------------------------------------------------------
# Header / framing


def test_parse_header_accepts_minimal_flv() -> None:
    assert list(parse_flv(io.BytesIO(make_flv([])))) == []


def test_parse_header_rejects_bad_signature() -> None:
    bogus = b"XYZ" + b"\x01\x05" + struct.pack(">I", 9) + b"\x00\x00\x00\x00"
    with pytest.raises(FlvParseError, match="bad signature"):
        list(parse_flv(io.BytesIO(bogus)))


def test_parse_header_truncated_raises() -> None:
    with pytest.raises(FlvParseError, match="truncated"):
        list(parse_flv(io.BytesIO(b"FLV")))


def test_parse_header_data_offset_less_than_min_raises() -> None:
    bogus = b"FLV" + bytes([1, 0x05]) + struct.pack(">I", 4) + b"\x00\x00\x00\x00"
    with pytest.raises(FlvParseError, match="data offset"):
        list(parse_flv(io.BytesIO(bogus)))


def test_parse_header_accepts_extra_padding() -> None:
    """`data_offset > FLV_HEADER_SIZE` is rare but legal; padding bytes
    are skipped before the initial back-reference."""
    header = b"FLV" + bytes([1, 0x05]) + struct.pack(">I", 12) + b"XXX" + b"\x00\x00\x00\x00"
    assert list(parse_flv(io.BytesIO(header))) == []


def test_truncated_tag_body_raises() -> None:
    truncated = (
        flv_header() + bytes([9]) + _u24_be(100) + _u24_be(0) + bytes([0]) + _u24_be(0) + b"\x90av"
    )
    with pytest.raises(FlvParseError, match="truncated"):
        list(parse_flv(io.BytesIO(truncated)))


def test_unrecognised_tag_type_raises() -> None:
    bad_tag = (
        bytes([99]) + _u24_be(0) + _u24_be(0) + bytes([0]) + _u24_be(0) + struct.pack(">I", 11)
    )
    with pytest.raises(FlvParseError, match="unrecognised tag type"):
        list(parse_flv(io.BytesIO(flv_header() + bad_tag)))


def test_timestamp_extension_byte_combines() -> None:
    """`timestamp_ms` is `(ts_ext << 24) | ts_lower` — verify wraparound
    above 24 bits is preserved by combining the extension byte."""
    body = video_legacy_body()
    tag = flv_tag(9, body, timestamp_ms=0x01FFFFFF)
    [parsed] = list(parse_flv(io.BytesIO(flv_header() + tag)))
    assert parsed.header.timestamp_ms == 0x01FFFFFF


# ---------------------------------------------------------------------------
# Video


def test_legacy_video_tag_recognised_as_legacy() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, video_legacy_body())]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "legacy"
    assert tag.body.codec_fourcc is None
    assert tag.body.track_id is None


def test_ertmp_non_multitrack_video_decodes_fourcc() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, video_ertmp_seqstart_body(b"hvc1"))]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp"
    assert tag.body.packet_type == 0
    assert tag.body.codec_fourcc == "hvc1"
    assert tag.body.track_id is None


@pytest.mark.parametrize(
    ("fourcc", "track_id"),
    [(b"avc1", 0), (b"hvc1", 1), (b"av01", 2), (b"avc1", 255)],
)
def test_one_track_multitrack_video_decodes_track_and_fourcc(fourcc: bytes, track_id: int) -> None:
    body = video_multitrack_one_track_body(fourcc=fourcc, track_id=track_id)
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, body)]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "one_track"
    assert tag.body.codec_fourcc == fourcc.decode("ascii")
    assert tag.body.track_id == track_id
    assert tag.body.inner_packet_type == 1


def test_many_tracks_multitrack_video_counted_but_not_decoded() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, video_multitrack_many_tracks_body())]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "many_tracks"
    assert tag.body.track_id is None
    assert tag.body.codec_fourcc is None


def test_video_multitrack_one_track_too_short_raises() -> None:
    short = bytes([0xA6, 0x00]) + b"avc"
    with pytest.raises(FlvParseError, match="ONE_TRACK video tag too short"):
        list(parse_flv(io.BytesIO(make_flv([flv_tag(9, short)]))))


def test_video_ertmp_too_short_for_fourcc_raises() -> None:
    short = bytes([0x90, 0x01])
    with pytest.raises(FlvParseError, match="E-RTMP video tag too short"):
        list(parse_flv(io.BytesIO(make_flv([flv_tag(9, short)]))))


def test_video_empty_body_raises() -> None:
    with pytest.raises(FlvParseError, match="video tag body is empty"):
        list(parse_flv(io.BytesIO(make_flv([flv_tag(9, b"")]))))


def test_unknown_multitrack_subtype_classified_as_unknown() -> None:
    """A reserved high-nibble value in the multitrack-type byte (e.g.
    0x30 or 0xF0, neither of which is in `MULTITRACKTYPE_*`) must not
    raise; we classify it as `unknown` so a future-spec stream is
    surfaced rather than rejected."""
    byte0 = VIDEO_ERTMP_MARKER | 0x20 | VIDEO_PACKETTYPE_MULTITRACK
    byte1 = 0x30 | 0x01  # reserved high nibble
    body = bytes([byte0, byte1]) + b"opaque-future-spec-data"
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, body)]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.multitrack_kind == "unknown"
    assert tag.body.track_id is None


# ---------------------------------------------------------------------------
# Audio


def test_legacy_audio_tag_recognised_as_legacy() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(8, audio_legacy_body())]))))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "legacy"
    assert tag.body.codec_fourcc is None


def test_ertmp_non_multitrack_audio_decodes_fourcc() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(8, audio_ertmp_seqstart_body(b"mp4a"))]))))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "ertmp"
    assert tag.body.packet_type == 0
    assert tag.body.codec_fourcc == "mp4a"


def test_one_track_multitrack_audio_decodes_track_and_fourcc() -> None:
    body = audio_multitrack_one_track_body(fourcc=b"mp4a", track_id=3)
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(8, body)]))))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "one_track"
    assert tag.body.codec_fourcc == "mp4a"
    assert tag.body.track_id == 3


def test_audio_empty_body_raises() -> None:
    with pytest.raises(FlvParseError, match="audio tag body is empty"):
        list(parse_flv(io.BytesIO(make_flv([flv_tag(8, b"")]))))


# ---------------------------------------------------------------------------
# Script


def test_script_tag_counted_not_parsed() -> None:
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(18, script_body())]))))
    assert tag.header.tag_type == 18
    assert isinstance(tag.body, ScriptTagInfo)


# ---------------------------------------------------------------------------
# analyse() aggregation


def test_analyse_aggregates_mixed_stream() -> None:
    data = make_flv(
        [
            flv_tag(18, script_body()),
            flv_tag(9, video_ertmp_seqstart_body(b"avc1")),
            flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)),
            flv_tag(9, video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            flv_tag(9, video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            flv_tag(8, audio_ertmp_seqstart_body(b"mp4a")),
            flv_tag(8, audio_multitrack_one_track_body(fourcc=b"mp4a", track_id=0)),
            flv_tag(8, audio_legacy_body()),
        ]
    )
    report = analyse(io.BytesIO(data))
    assert report.total_tags == 8
    assert report.video_tags == 4
    assert report.audio_tags == 3
    assert report.script_tags == 1
    assert report.ertmp_video == 1
    assert report.multitrack_video == 3
    assert report.legacy_video == 0
    assert report.ertmp_audio == 1
    assert report.multitrack_audio == 1
    assert report.legacy_audio == 1
    assert report.tracks_seen[(0, "avc1", "video")] == 1
    assert report.tracks_seen[(1, "hvc1", "video")] == 2
    assert report.tracks_seen[(0, "mp4a", "audio")] == 1


def test_analyse_to_json_round_trip() -> None:
    data = make_flv([flv_tag(9, video_multitrack_one_track_body(fourcc=b"av01", track_id=2))])
    blob = json.dumps(analyse(io.BytesIO(data)).to_json())
    decoded = json.loads(blob)
    assert decoded["multitrack_video"] == 1
    assert decoded["tracks_seen"] == [
        {"track_id": 2, "codec_fourcc": "av01", "kind": "video", "count": 1}
    ]


# ---------------------------------------------------------------------------
# Hypothesis property tests — slice E1's fuzz pass is the formal home,
# but a small property suite here catches regressions in the parser
# even before E1 lands.


@dataclass(frozen=True)
class _TagSpec:
    """A property-test description of one synthetic tag."""

    flavor: str  # "legacy_v", "ertmp_v", "mt1_v", "mtN_v", "legacy_a", "ertmp_a", "mt1_a", "script"
    track_id: int
    fourcc: bytes


_FOURCCS = (b"avc1", b"hvc1", b"av01")
_AUDIO_FOURCCS = (b"mp4a",)


@st.composite
def _tag_spec(draw: st.DrawFn) -> _TagSpec:
    flavor = draw(
        st.sampled_from(
            ("legacy_v", "ertmp_v", "mt1_v", "mtN_v", "legacy_a", "ertmp_a", "mt1_a", "script")
        )
    )
    track_id = draw(st.integers(min_value=0, max_value=255))
    is_audio = flavor.endswith("_a") and "mt1" in flavor or flavor in {"ertmp_a", "legacy_a"}
    fourcc = draw(st.sampled_from(_AUDIO_FOURCCS if is_audio else _FOURCCS))
    return _TagSpec(flavor=flavor, track_id=track_id, fourcc=fourcc)


def _materialise(spec: _TagSpec) -> bytes:
    if spec.flavor == "legacy_v":
        return flv_tag(9, video_legacy_body())
    if spec.flavor == "ertmp_v":
        return flv_tag(9, video_ertmp_seqstart_body(spec.fourcc))
    if spec.flavor == "mt1_v":
        body = video_multitrack_one_track_body(fourcc=spec.fourcc, track_id=spec.track_id)
        return flv_tag(9, body)
    if spec.flavor == "mtN_v":
        return flv_tag(9, video_multitrack_many_tracks_body())
    if spec.flavor == "legacy_a":
        return flv_tag(8, audio_legacy_body())
    if spec.flavor == "ertmp_a":
        return flv_tag(8, audio_ertmp_seqstart_body(spec.fourcc))
    if spec.flavor == "mt1_a":
        body = audio_multitrack_one_track_body(fourcc=spec.fourcc, track_id=spec.track_id)
        return flv_tag(8, body)
    if spec.flavor == "script":
        return flv_tag(18, script_body())
    raise AssertionError(f"unknown spec flavor {spec.flavor}")


@given(specs=st.lists(_tag_spec(), max_size=30))
@settings(max_examples=200, deadline=None)
def test_parse_count_matches_emit_count(specs: list[_TagSpec]) -> None:
    """Any sequence of valid synthetic tags parses to exactly that many tags."""
    data = flv_header() + b"".join(_materialise(s) for s in specs)
    parsed = list(parse_flv(io.BytesIO(data)))
    assert len(parsed) == len(specs)


@given(specs=st.lists(_tag_spec(), min_size=1, max_size=10), cut=st.integers(min_value=1))
@settings(max_examples=100, deadline=None)
def test_truncation_anywhere_yields_flvparseerror_or_partial(
    specs: list[_TagSpec], cut: int
) -> None:
    """Any prefix of a valid FLV either parses to a partial set of tags
    OR raises FlvParseError. Never a non-FlvParseError exception."""
    data = flv_header() + b"".join(_materialise(s) for s in specs)
    if cut >= len(data):
        return  # not a truncation
    truncated = data[:cut]
    try:
        list(parse_flv(io.BytesIO(truncated)))
    except FlvParseError:
        pass  # expected on most cut points
    except Exception as e:
        raise AssertionError(f"unexpected exception class {type(e).__name__}: {e}") from e


@given(track_id=st.integers(min_value=0, max_value=255), fourcc=st.sampled_from(_FOURCCS))
@settings(max_examples=50, deadline=None)
def test_one_track_multitrack_video_round_trip(track_id: int, fourcc: bytes) -> None:
    """Any (track_id, fourcc) pair produced by the synthetic builder
    decodes back to the same pair."""
    body = video_multitrack_one_track_body(fourcc=fourcc, track_id=track_id)
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, body)]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.track_id == track_id
    assert tag.body.codec_fourcc == fourcc.decode("ascii")


@given(byte0=st.integers(min_value=0, max_value=255))
@settings(max_examples=256, deadline=None)
def test_video_byte0_high_bit_classifies_flavor(byte0: int) -> None:
    """A video tag's flavor is determined exclusively by byte 0's high bit:
    set → ertmp-family, clear → legacy. Tags with high-bit set + non-MULTITRACK
    packet_type need 4 more bytes for the FourCC; tags with packet_type=6 need
    more for the multitrack body. We synthesise the minimum body size for
    each case so the classification path runs to completion."""
    if byte0 & VIDEO_ERTMP_MARKER:
        packet_type = byte0 & VIDEO_PACKET_TYPE_MASK
        if packet_type == VIDEO_PACKETTYPE_MULTITRACK:
            # ONE_TRACK body: byte0 + byte1=ONE_TRACK|inner + 4-byte fcc + track_id
            body = bytes([byte0, MULTITRACK_TYPE_ONE_TRACK | 0x01]) + b"avc1" + b"\x00"
            expected_flavor = "ertmp_multitrack"
        else:
            body = bytes([byte0]) + b"avc1"
            expected_flavor = "ertmp"
    else:
        body = bytes([byte0]) + b"\x00\x00\x00\x00\x00"
        expected_flavor = "legacy"
    [tag] = list(parse_flv(io.BytesIO(make_flv([flv_tag(9, body)]))))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == expected_flavor
