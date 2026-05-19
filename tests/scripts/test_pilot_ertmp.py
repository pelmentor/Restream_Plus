"""Tests for `scripts/pilot_ertmp.py`.

The FLV byte layout under test is documented in
`reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501` (video multitrack)
and `:388-432` (audio multitrack). Each test builds a synthetic FLV stream
matching one of those layouts and asserts the parser returns the expected
flavor / FourCC / track_id triple.
"""

from __future__ import annotations

import io
import json
import struct
from collections.abc import Iterable
from pathlib import Path

import pytest
from scripts.pilot_ertmp import (
    AUDIO_ERTMP_HIGH_NIBBLE,
    AUDIO_PACKETTYPE_MULTITRACK,
    MULTITRACK_TYPE_MANY_TRACKS,
    MULTITRACK_TYPE_ONE_TRACK,
    VIDEO_ERTMP_MARKER,
    VIDEO_PACKETTYPE_MULTITRACK,
    AudioTagInfo,
    FlvParseError,
    VideoTagInfo,
    analyse,
    main,
    parse_flv,
)

# ---------------------------------------------------------------------------
# Synthetic FLV builder


def _u24_be(value: int) -> bytes:
    return struct.pack(">I", value)[1:]


def _flv_header(*, audio: bool = True, video: bool = True) -> bytes:
    flags = 0
    if audio:
        flags |= 0x04
    if video:
        flags |= 0x01
    return b"FLV" + bytes([1, flags]) + struct.pack(">I", 9) + b"\x00\x00\x00\x00"


def _flv_tag(tag_type: int, body: bytes, timestamp_ms: int = 0) -> bytes:
    data_size = len(body)
    ts_lower = timestamp_ms & 0xFFFFFF
    ts_ext = (timestamp_ms >> 24) & 0xFF
    header = (
        bytes([tag_type]) + _u24_be(data_size) + _u24_be(ts_lower) + bytes([ts_ext]) + _u24_be(0)
    )
    prev = struct.pack(">I", 11 + data_size)
    return header + body + prev


def _video_legacy_body() -> bytes:
    # frame_type=KEY (1<<4=0x10) | codec_id=H.264 (7)
    return bytes([0x17]) + b"\x00\x00\x00\x00\x00fake-h264-payload"


def _video_ertmp_seqstart_body(fourcc: bytes = b"avc1") -> bytes:
    # byte0: 0x80 (E-RTMP) | 0x10 (KEY) | 0x00 (SEQ_START) = 0x90
    return bytes([0x90]) + fourcc + b"fake-seq-start-payload"


def _video_multitrack_one_track_body(
    *, fourcc: bytes, track_id: int, inner_packet_type: int = 1
) -> bytes:
    # byte0: 0x80 (E-RTMP) | 0x20 (INTER) | 0x06 (MULTITRACK) = 0xA6
    byte0 = VIDEO_ERTMP_MARKER | 0x20 | VIDEO_PACKETTYPE_MULTITRACK
    # byte1: MULTITRACK_TYPE_ONE_TRACK (0x00) | inner_packet_type
    byte1 = MULTITRACK_TYPE_ONE_TRACK | inner_packet_type
    return bytes([byte0, byte1]) + fourcc + bytes([track_id]) + b"fake-payload"


def _video_multitrack_many_tracks_body() -> bytes:
    byte0 = VIDEO_ERTMP_MARKER | 0x20 | VIDEO_PACKETTYPE_MULTITRACK
    byte1 = MULTITRACK_TYPE_MANY_TRACKS | 0x01
    # MANY_TRACKS layout is variable; we just emit enough to count it.
    return bytes([byte0, byte1]) + b"opaque-many-tracks-data"


def _audio_legacy_body() -> bytes:
    # sound_format=10 (AAC, 0xA0) | sound_rate=3 (44.1kHz, 0x0C) |
    # size=1 (16-bit, 0x02) | type=1 (stereo, 0x01)
    return bytes([0xAF, 0x01]) + b"fake-aac-payload"


def _audio_ertmp_seqstart_body(fourcc: bytes = b"mp4a") -> bytes:
    # byte0: AUDIO_ERTMP_HIGH_NIBBLE (0x90) | SEQ_START (0)
    return bytes([AUDIO_ERTMP_HIGH_NIBBLE]) + fourcc + b"fake-aac-seq-start"


def _audio_multitrack_one_track_body(*, fourcc: bytes, track_id: int) -> bytes:
    byte0 = AUDIO_ERTMP_HIGH_NIBBLE | AUDIO_PACKETTYPE_MULTITRACK
    byte1 = MULTITRACK_TYPE_ONE_TRACK | 0x01  # inner = FRAMES
    return bytes([byte0, byte1]) + fourcc + bytes([track_id]) + b"audio-frame-payload"


def _script_body() -> bytes:
    # Opaque AMF — we don't decode script tags, just count them.
    return b"\x02\x00\x0aonMetaData\x05"


def _make_flv(tags: Iterable[bytes]) -> bytes:
    return _flv_header() + b"".join(tags)


# ---------------------------------------------------------------------------
# Header / framing tests


def test_parse_header_accepts_minimal_flv() -> None:
    stream = io.BytesIO(_make_flv([]))
    tags = list(parse_flv(stream))
    assert tags == []


def test_parse_header_rejects_bad_signature() -> None:
    bogus = b"XYZ" + b"\x01\x05" + struct.pack(">I", 9) + b"\x00\x00\x00\x00"
    with pytest.raises(FlvParseError, match="bad signature"):
        list(parse_flv(io.BytesIO(bogus)))


def test_parse_header_truncated_raises() -> None:
    with pytest.raises(FlvParseError, match="truncated"):
        list(parse_flv(io.BytesIO(b"FLV")))


def test_truncated_tag_body_raises() -> None:
    # Tag header advertises 100-byte body but only 4 are present.
    truncated = (
        _flv_header()
        + bytes([9])  # tag_type
        + _u24_be(100)  # data_size
        + _u24_be(0)
        + bytes([0])
        + _u24_be(0)
        + b"\x90av"  # only 3 bytes of body
    )
    with pytest.raises(FlvParseError, match="truncated"):
        list(parse_flv(io.BytesIO(truncated)))


def test_unrecognised_tag_type_raises() -> None:
    bad_tag = (
        bytes([99]) + _u24_be(0) + _u24_be(0) + bytes([0]) + _u24_be(0) + struct.pack(">I", 11)
    )
    with pytest.raises(FlvParseError, match="unrecognised tag type"):
        list(parse_flv(io.BytesIO(_flv_header() + bad_tag)))


# ---------------------------------------------------------------------------
# Video tag tests


def test_legacy_video_tag_recognised_as_legacy() -> None:
    data = _make_flv([_flv_tag(9, _video_legacy_body())])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "legacy"
    assert tag.body.codec_fourcc is None
    assert tag.body.track_id is None


def test_ertmp_non_multitrack_video_decodes_fourcc() -> None:
    data = _make_flv([_flv_tag(9, _video_ertmp_seqstart_body(b"hvc1"))])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp"
    assert tag.body.packet_type == 0  # SEQ_START
    assert tag.body.codec_fourcc == "hvc1"
    assert tag.body.track_id is None


@pytest.mark.parametrize(
    ("fourcc", "track_id"),
    [(b"avc1", 0), (b"hvc1", 1), (b"av01", 2), (b"avc1", 255)],
)
def test_one_track_multitrack_video_decodes_track_and_fourcc(fourcc: bytes, track_id: int) -> None:
    body = _video_multitrack_one_track_body(fourcc=fourcc, track_id=track_id)
    data = _make_flv([_flv_tag(9, body)])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "one_track"
    assert tag.body.codec_fourcc == fourcc.decode("ascii")
    assert tag.body.track_id == track_id
    assert tag.body.inner_packet_type == 1  # FRAMES


def test_many_tracks_multitrack_video_counted_but_not_decoded() -> None:
    data = _make_flv([_flv_tag(9, _video_multitrack_many_tracks_body())])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, VideoTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "many_tracks"
    assert tag.body.track_id is None
    assert tag.body.codec_fourcc is None


def test_video_multitrack_one_track_too_short_raises() -> None:
    short = bytes([0xA6, 0x00]) + b"avc"  # only 3 of 4 FourCC bytes, no track_id
    data = _make_flv([_flv_tag(9, short)])
    with pytest.raises(FlvParseError, match="ONE_TRACK video tag too short"):
        list(parse_flv(io.BytesIO(data)))


def test_video_ertmp_too_short_for_fourcc_raises() -> None:
    short = bytes([0x90, 0x01])  # E-RTMP marker, then partial FourCC
    data = _make_flv([_flv_tag(9, short)])
    with pytest.raises(FlvParseError, match="E-RTMP video tag too short"):
        list(parse_flv(io.BytesIO(data)))


def test_video_empty_body_raises() -> None:
    data = _make_flv([_flv_tag(9, b"")])
    with pytest.raises(FlvParseError, match="video tag body is empty"):
        list(parse_flv(io.BytesIO(data)))


# ---------------------------------------------------------------------------
# Audio tag tests


def test_legacy_audio_tag_recognised_as_legacy() -> None:
    data = _make_flv([_flv_tag(8, _audio_legacy_body())])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "legacy"
    assert tag.body.codec_fourcc is None


def test_ertmp_non_multitrack_audio_decodes_fourcc() -> None:
    data = _make_flv([_flv_tag(8, _audio_ertmp_seqstart_body(b"mp4a"))])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "ertmp"
    assert tag.body.packet_type == 0  # SEQ_START
    assert tag.body.codec_fourcc == "mp4a"


def test_one_track_multitrack_audio_decodes_track_and_fourcc() -> None:
    data = _make_flv([_flv_tag(8, _audio_multitrack_one_track_body(fourcc=b"mp4a", track_id=3))])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert isinstance(tag.body, AudioTagInfo)
    assert tag.body.flavor == "ertmp_multitrack"
    assert tag.body.multitrack_kind == "one_track"
    assert tag.body.codec_fourcc == "mp4a"
    assert tag.body.track_id == 3


# ---------------------------------------------------------------------------
# Script tag


def test_script_tag_counted_not_parsed() -> None:
    data = _make_flv([_flv_tag(18, _script_body())])
    [tag] = list(parse_flv(io.BytesIO(data)))
    assert tag.header.tag_type == 18


# ---------------------------------------------------------------------------
# analyse() summary


def test_analyse_aggregates_mixed_stream() -> None:
    data = _make_flv(
        [
            _flv_tag(18, _script_body()),
            _flv_tag(9, _video_ertmp_seqstart_body(b"avc1")),
            _flv_tag(9, _video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)),
            _flv_tag(9, _video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            _flv_tag(9, _video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            _flv_tag(8, _audio_ertmp_seqstart_body(b"mp4a")),
            _flv_tag(8, _audio_multitrack_one_track_body(fourcc=b"mp4a", track_id=0)),
            _flv_tag(8, _audio_legacy_body()),
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
    data = _make_flv([_flv_tag(9, _video_multitrack_one_track_body(fourcc=b"av01", track_id=2))])
    report = analyse(io.BytesIO(data))
    blob = json.dumps(report.to_json())
    decoded = json.loads(blob)
    assert decoded["multitrack_video"] == 1
    assert decoded["tracks_seen"] == [
        {"track_id": 2, "codec_fourcc": "av01", "kind": "video", "count": 1}
    ]


# ---------------------------------------------------------------------------
# CLI surface


def test_cli_human_summary_on_valid_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "capture.flv"
    capture.write_bytes(
        _make_flv(
            [
                _flv_tag(9, _video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)),
                _flv_tag(9, _video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            ]
        )
    )
    rc = main([str(capture)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "total_tags        = 2" in out
    assert "multitrack=2" in out
    assert "track_id=0" in out
    assert "track_id=1" in out


def test_cli_json_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capture = tmp_path / "capture.flv"
    capture.write_bytes(
        _make_flv([_flv_tag(9, _video_multitrack_one_track_body(fourcc=b"avc1", track_id=0))])
    )
    rc = main([str(capture), "--json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["multitrack_video"] == 1
    assert parsed["tracks_seen"][0]["track_id"] == 0


def test_cli_verbose_dump_does_not_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "capture.flv"
    capture.write_bytes(_make_flv([_flv_tag(9, _video_legacy_body())]))
    rc = main([str(capture), "--verbose"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "video flavor=legacy" in out


def test_cli_returns_1_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(tmp_path / "nope.flv")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "is not a file" in err


def test_cli_returns_2_on_parse_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bogus = tmp_path / "bogus.flv"
    bogus.write_bytes(b"XYZ\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00")
    rc = main([str(bogus)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "parse error" in err
