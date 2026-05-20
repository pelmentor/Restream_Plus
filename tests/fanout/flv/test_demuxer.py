"""Tests for `app.fanout.flv.demuxer` — the push-style FLV parser.

Equivalence is the core property: feeding a stream through the push
demuxer (in any chunking) must yield the same tags the pull parser
(`parse_flv`) produces over the whole buffer. The synthetic FLV builders
are reused from `test_parser` so both parsers are exercised against the
identical OBS-anchored byte layouts.
"""

from __future__ import annotations

import io

import pytest
from app.fanout.flv.demuxer import FlvDemuxer
from app.fanout.flv.parser import (
    FLV_HEADER_SIZE,
    PREVIOUS_TAG_SIZE_BYTES,
    AudioTagInfo,
    FlvParseError,
    VideoTagInfo,
    parse_flv,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.fanout.flv.test_parser import (
    audio_legacy_body,
    audio_multitrack_one_track_body,
    flv_header,
    flv_tag,
    make_flv,
    script_body,
    video_ertmp_seqstart_body,
    video_legacy_body,
    video_multitrack_one_track_body,
)


def _mixed_stream() -> bytes:
    return make_flv(
        [
            flv_tag(18, script_body()),
            flv_tag(9, video_ertmp_seqstart_body(b"avc1")),
            flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)),
            flv_tag(9, video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
            flv_tag(8, audio_multitrack_one_track_body(fourcc=b"mp4a", track_id=0)),
        ]
    )


def _tag_signature(tags: list) -> list[tuple]:  # type: ignore[type-arg]
    """Reduce tags to a comparable (type, flavor, fourcc, track_id) tuple list."""
    sig = []
    for t in tags:
        body = t.body
        if isinstance(body, VideoTagInfo | AudioTagInfo):
            sig.append((t.header.tag_type, body.flavor, body.codec_fourcc, body.track_id))
        else:
            sig.append((t.header.tag_type, "script", None, None))
    return sig


# ---------------------------------------------------------------------------
# Equivalence with the pull parser


def test_whole_stream_feed_matches_pull_parser() -> None:
    data = _mixed_stream()
    dem = FlvDemuxer()
    push_tags = dem.feed(data)
    pull_tags = list(parse_flv(io.BytesIO(data)))
    assert _tag_signature(push_tags) == _tag_signature(pull_tags)


def test_byte_by_byte_feed_matches_pull_parser() -> None:
    data = _mixed_stream()
    dem = FlvDemuxer()
    collected: list = []  # type: ignore[type-arg]
    for i in range(len(data)):
        collected.extend(dem.feed(data[i : i + 1]))
    pull_tags = list(parse_flv(io.BytesIO(data)))
    assert _tag_signature(collected) == _tag_signature(pull_tags)


@given(chunk=st.integers(min_value=1, max_value=len(_mixed_stream())))
@settings(max_examples=200, deadline=None)
def test_arbitrary_chunking_matches_pull_parser(chunk: int) -> None:
    # Chunk size ranges from 1 byte up to the whole stream in one feed, so the
    # split boundary lands on every offset including exact tag-header
    # boundaries — catches a framing off-by-one the fixed-size feeds might miss.
    data = _mixed_stream()
    dem = FlvDemuxer()
    collected: list = []  # type: ignore[type-arg]
    for i in range(0, len(data), chunk):
        collected.extend(dem.feed(data[i : i + chunk]))
    pull_tags = list(parse_flv(io.BytesIO(data)))
    assert _tag_signature(collected) == _tag_signature(pull_tags)


# ---------------------------------------------------------------------------
# Partial / framing behaviour


def test_partial_trailing_tag_is_retained_then_completed() -> None:
    data = _mixed_stream()
    dem = FlvDemuxer()
    # Feed all but the last 3 bytes — the final tag is incomplete.
    first = dem.feed(data[:-3])
    # Feed the remaining bytes — the final tag completes.
    rest = dem.feed(data[-3:])
    pull_tags = list(parse_flv(io.BytesIO(data)))
    assert _tag_signature(first + rest) == _tag_signature(pull_tags)
    assert len(rest) == 1  # exactly the last tag emerged on completion


def test_header_split_across_feeds() -> None:
    data = _mixed_stream()
    # The header isn't consumable until data_offset(=FLV_HEADER_SIZE here) + the
    # 4-byte PreviousTagSize0 are buffered. Split strictly before that boundary
    # so the first two feeds yield nothing, then the third completes header+tags.
    header_need = FLV_HEADER_SIZE + PREVIOUS_TAG_SIZE_BYTES  # 13 for a minimal header
    dem = FlvDemuxer()
    assert dem.feed(data[:5]) == []  # header incomplete
    assert dem.feed(data[5 : header_need - 1]) == []  # still one byte short of header+backref
    tail = dem.feed(data[header_need - 1 :])
    assert len(tail) == 5


def test_empty_feed_yields_nothing() -> None:
    dem = FlvDemuxer()
    assert dem.feed(b"") == []
    assert dem.feed(flv_header()) == []  # header only, no tags yet


# ---------------------------------------------------------------------------
# stream_mode latching (single-track fast path)


def test_stream_mode_unknown_before_media_tag() -> None:
    dem = FlvDemuxer()
    dem.feed(flv_header())
    assert dem.stream_mode == "unknown"
    dem.feed(flv_tag(18, script_body()))
    assert dem.stream_mode == "unknown"  # script tag doesn't latch


def test_stream_mode_latches_single_track_on_legacy_video() -> None:
    dem = FlvDemuxer()
    dem.feed(make_flv([flv_tag(9, video_legacy_body())]))
    assert dem.stream_mode == "single_track"


def test_stream_mode_latches_single_track_on_ertmp_nonmultitrack() -> None:
    dem = FlvDemuxer()
    dem.feed(make_flv([flv_tag(9, video_ertmp_seqstart_body(b"avc1"))]))
    assert dem.stream_mode == "single_track"


def test_stream_mode_latches_multi_track() -> None:
    dem = FlvDemuxer()
    dem.feed(make_flv([flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0))]))
    assert dem.stream_mode == "multi_track"


def test_stream_mode_does_not_flip_after_latch() -> None:
    dem = FlvDemuxer()
    # First a single-track tag latches single_track...
    dem.feed(make_flv([flv_tag(9, video_legacy_body())]))
    assert dem.stream_mode == "single_track"
    # ...a later multi-track tag must NOT flip the latch.
    dem.feed(flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)))
    assert dem.stream_mode == "single_track"


def test_stream_mode_latches_on_audio_too() -> None:
    dem = FlvDemuxer()
    dem.feed(make_flv([flv_tag(8, audio_legacy_body())]))
    assert dem.stream_mode == "single_track"


# ---------------------------------------------------------------------------
# Errors / lifecycle


def test_bad_signature_raises() -> None:
    dem = FlvDemuxer()
    with pytest.raises(FlvParseError, match="bad signature"):
        dem.feed(b"XYZ\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00")


def test_unrecognised_tag_type_raises() -> None:
    dem = FlvDemuxer()
    # tag_type=99, data_size=0, ts/stream zero, 4-byte back-ref.
    bad_tag = bytes([99]) + b"\x00\x00\x00" + b"\x00\x00\x00\x00\x00\x00\x00" + b"\x00\x00\x00\x0b"
    with pytest.raises(FlvParseError, match="unrecognised tag type"):
        dem.feed(flv_header() + bad_tag)


def test_feed_after_close_raises() -> None:
    dem = FlvDemuxer()
    dem.feed(flv_header())
    dem.close()
    with pytest.raises(FlvParseError, match="after close"):
        dem.feed(b"more")


def test_close_discards_partial_tail() -> None:
    data = _mixed_stream()
    dem = FlvDemuxer()
    dem.feed(data[:-3])  # incomplete final tag buffered
    dem.close()  # must not raise; partial bytes discarded
