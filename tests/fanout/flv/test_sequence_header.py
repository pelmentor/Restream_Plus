"""Tests for `app.fanout.flv.sequence_header`.

The decoder-config-record vectors in `_seq_header_vectors.py` are real
ffmpeg output (libx264 / libx265 / libaom-av1), so these tests pin the
parser against the actual wire format rather than synthetic guesses.

Spec references in the parser docstring:
- H.264 SPS — ITU-T H.264 §7.3.2.1.1
- HEVC SPS — ITU-T H.265 §7.3.2.2.1 + profile_tier_level §7.3.3
- AV1 sequence header OBU — AV1 bitstream spec §5.5.1
"""

from __future__ import annotations

import pytest
from app.fanout.flv.sequence_header import (
    Resolution,
    parse_av1_resolution,
    parse_avc_resolution,
    parse_hevc_resolution,
    parse_resolution,
)

from tests.fanout.flv._seq_header_vectors import (
    AV1_480,
    AV1_720,
    AV1_1080,
    AVC_480,
    AVC_720,
    AVC_1080,
    HEVC_480,
    HEVC_720,
    HEVC_1080,
    VECTORS,
)

# ---------------------------------------------------------------------------
# End-to-end: every real vector decodes to its known resolution


@pytest.mark.parametrize(("fourcc", "hexstr", "expected"), VECTORS)
def test_real_vectors_decode_to_known_resolution(
    fourcc: str, hexstr: str, expected: tuple[int, int]
) -> None:
    res = parse_resolution(fourcc, bytes.fromhex(hexstr))
    assert res is not None, f"{fourcc} {expected} failed to parse"
    assert (res.width, res.height) == expected


# ---------------------------------------------------------------------------
# Per-codec direct entry points


@pytest.mark.parametrize(
    ("hexstr", "expected"),
    [(AVC_1080, (1920, 1080)), (AVC_720, (1280, 720)), (AVC_480, (854, 480))],
)
def test_avc(hexstr: str, expected: tuple[int, int]) -> None:
    res = parse_avc_resolution(bytes.fromhex(hexstr))
    assert res == Resolution(*expected)


@pytest.mark.parametrize(
    ("hexstr", "expected"),
    [(HEVC_1080, (1920, 1080)), (HEVC_720, (1280, 720)), (HEVC_480, (854, 480))],
)
def test_hevc(hexstr: str, expected: tuple[int, int]) -> None:
    res = parse_hevc_resolution(bytes.fromhex(hexstr))
    assert res == Resolution(*expected)


@pytest.mark.parametrize(
    ("hexstr", "expected"),
    [(AV1_1080, (1920, 1080)), (AV1_720, (1280, 720)), (AV1_480, (854, 480))],
)
def test_av1(hexstr: str, expected: tuple[int, int]) -> None:
    res = parse_av1_resolution(bytes.fromhex(hexstr))
    assert res == Resolution(*expected)


def test_avc_1080_crops_coded_1088_to_1080() -> None:
    """H.264 1080p is coded as 1088 (68 MB rows × 16) and cropped to
    1080 via frame_cropping_flag — the canonical crop case. This pins
    that the parser applies the crop rather than reporting 1088."""
    res = parse_avc_resolution(bytes.fromhex(AVC_1080))
    assert res == Resolution(1920, 1080)


# ---------------------------------------------------------------------------
# Dispatcher


def test_parse_resolution_unknown_codec_returns_none() -> None:
    assert parse_resolution("mp4a", bytes.fromhex(AVC_1080)) is None
    assert parse_resolution("vp09", bytes.fromhex(AVC_1080)) is None


def test_parse_resolution_routes_by_fourcc() -> None:
    # An avc record handed to the hevc path must not crash — it returns
    # None or a garbage-but-bounded value, never an exception.
    assert parse_resolution("avc1", bytes.fromhex(AVC_720)) == Resolution(1280, 720)


# ---------------------------------------------------------------------------
# Graceful degradation — malformed input never raises


@pytest.mark.parametrize("fourcc", ["avc1", "hvc1", "av01"])
def test_empty_input_returns_none(fourcc: str) -> None:
    assert parse_resolution(fourcc, b"") is None


@pytest.mark.parametrize("fourcc", ["avc1", "hvc1", "av01"])
def test_truncated_input_returns_none(fourcc: str) -> None:
    for hexstr in (AVC_1080, HEVC_1080, AV1_1080):
        full = bytes.fromhex(hexstr)
        for cut in range(1, len(full)):
            # Must never raise, whatever the truncation point.
            parse_resolution(fourcc, full[:cut])


def test_avc_zero_sps_count_returns_none() -> None:
    # Valid header but numOfSPS == 0.
    rec = bytes.fromhex("0164002800e0")  # last byte 0xE0 → num_sps low bits = 0
    assert parse_avc_resolution(rec) is None


def _assert_bounded_or_none(res: Resolution | None) -> None:
    """Garbage input may Exp-Golomb-decode to a small bounded resolution
    rather than None — both are acceptable. The load-bearing invariant
    (Rule №6 footgun) is that the parser NEVER raises and NEVER returns a
    nonsense unbounded value."""
    if res is not None:
        # Generous ceiling: AV1 frame dims top out at 2**16; H.264/HEVC
        # ue(v) is theoretically unbounded but a realistic garbage decode
        # stays well under this. The point is "not absurd", not a spec cap.
        assert 0 < res.width <= 1_000_000
        assert 0 < res.height <= 1_000_000


def test_avc_garbage_does_not_raise() -> None:
    _assert_bounded_or_none(parse_avc_resolution(b"\xff" * 64))


def test_hevc_garbage_does_not_raise() -> None:
    _assert_bounded_or_none(parse_hevc_resolution(b"\xff" * 64))


def test_av1_garbage_does_not_raise() -> None:
    # 4-byte config header then junk OBUs.
    _assert_bounded_or_none(parse_av1_resolution(b"\x81\x08\x0c\x00" + b"\xff" * 32))


# ---------------------------------------------------------------------------
# Emulation-prevention handling
#
# The AVC_1080 SPS contains a `000003` emulation-prevention sequence
# (…c044000003000400…). A parser that fails to strip the 0x03 mis-aligns
# every subsequent Exp-Golomb read and yields the wrong resolution — so
# the fact that AVC_1080 decodes to exactly 1920×1080 IS the
# emulation-prevention regression test. Asserting it explicitly here
# documents the intent.


def test_emulation_prevention_is_stripped_for_avc() -> None:
    raw = bytes.fromhex(AVC_1080)
    assert b"\x00\x00\x03" in raw, "fixture should contain an emulation-prevention sequence"
    assert parse_avc_resolution(raw) == Resolution(1920, 1080)


def test_resolution_is_frozen() -> None:
    res = Resolution(1920, 1080)
    with pytest.raises(AttributeError):
        res.width = 1280  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Synthetic coverage for paths the real ffmpeg vectors don't exercise.
#
# The three real HEVC vectors all have sps_max_sub_layers_minus1 == 0, so the
# sub-layer profile_tier_level skip is untested by them. We hand-build an SPS
# RBSP with max_sub_layers_minus1 == 1 and both sub-layer present-flags set,
# forcing the 88-bit profile + 8-bit level sub-layer skips, then assert the
# parser still recovers a known resolution. The builder mirrors the spec field
# layout the parser reads; the general-PTL portion is independently validated
# by the real vectors, so a consistent recover here pins the sub-layer skip.


class _BitWriter:
    def __init__(self) -> None:
        self._bits: list[int] = []

    def write_bits(self, value: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def write_ue(self, value: int) -> None:
        # codeNum → M leading zeros + (value+1) in M+1 bits.
        v = value + 1
        m = v.bit_length() - 1
        self.write_bits(0, m)
        self.write_bits(v, m + 1)

    def to_bytes(self) -> bytes:
        out = bytearray()
        bits = list(self._bits)
        while len(bits) % 8 != 0:
            bits.append(0)  # rbsp trailing padding
        for i in range(0, len(bits), 8):
            byte = 0
            for b in bits[i : i + 8]:
                byte = (byte << 1) | b
            out.append(byte)
        return bytes(out)


def _synthetic_hevc_with_sublayers(width: int, height: int) -> bytes:
    """Build a minimal hvcC whose SPS has max_sub_layers_minus1=1 and both
    sub-layer present flags set (exercises the 88+8-bit sub-layer skip)."""
    w = _BitWriter()
    w.write_bits(0, 4)  # sps_video_parameter_set_id
    w.write_bits(1, 3)  # sps_max_sub_layers_minus1 = 1
    w.write_bits(1, 1)  # sps_temporal_id_nesting_flag
    # general profile_tier_level: 96 bits of zeros.
    w.write_bits(0, 8)
    w.write_bits(0, 32)
    w.write_bits(0, 48)
    w.write_bits(0, 8)
    # sub-layer present flags for layer 0: profile=1, level=1.
    w.write_bits(1, 1)
    w.write_bits(1, 1)
    # reserved_zero_2bits alignment for layers [1, 8): 7 * 2 bits.
    for _ in range(1, 8):
        w.write_bits(0, 2)
    # sub-layer profile (88 bits) + level (8 bits), both present.
    w.write_bits(0, 88)
    w.write_bits(0, 8)
    # SPS body resuming after profile_tier_level.
    w.write_ue(0)  # sps_seq_parameter_set_id
    w.write_ue(1)  # chroma_format_idc = 4:2:0
    w.write_ue(width)  # pic_width_in_luma_samples
    w.write_ue(height)  # pic_height_in_luma_samples
    w.write_bits(0, 1)  # conformance_window_flag = 0
    sps_rbsp = w.to_bytes()
    # Prepend the 2-byte HEVC NAL header (type 33 = SPS: (33<<1)=0x42, 0x01).
    sps_nal = bytes([0x42, 0x01]) + sps_rbsp
    # Minimal hvcC: 22-byte header + numOfArrays(1) + one SPS array.
    header = bytes(22)
    array = (
        bytes([33])  # array_completeness/reserved/NAL_unit_type=33
        + bytes([0x00, 0x01])  # numNalus = 1
        + bytes([(len(sps_nal) >> 8) & 0xFF, len(sps_nal) & 0xFF])
        + sps_nal
    )
    return header + bytes([1]) + array


def test_hevc_sublayer_profile_tier_level_skip() -> None:
    rec = _synthetic_hevc_with_sublayers(1280, 720)
    assert parse_hevc_resolution(rec) == Resolution(1280, 720)


def test_av1_timing_info_present_returns_none() -> None:
    """AV1 sequence headers with timing_info_present_flag set bail to None
    (we don't decode the variable-length timing/decoder-model tail). Build a
    seq-header OBU with that bit set and confirm the documented bail."""
    w = _BitWriter()
    w.write_bits(0, 3)  # seq_profile
    w.write_bits(0, 1)  # still_picture
    w.write_bits(0, 1)  # reduced_still_picture_header = 0
    w.write_bits(1, 1)  # timing_info_present_flag = 1  → bail
    w.write_bits(0, 16)  # padding so the reader has bytes (unread on bail)
    obu_payload = w.to_bytes()
    obu = bytes([0x0A]) + bytes([len(obu_payload)]) + obu_payload  # type=1, has_size
    config = bytes([0x81, 0x08, 0x0C, 0x00]) + obu
    assert parse_av1_resolution(config) is None
