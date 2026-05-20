"""Codec sequence-header → resolution extraction (ADR-0016 slice B4).

The Enhanced-RTMP video `PACKETTYPE_SEQ_START` tag carries the codec's
decoder-configuration record (the same bytes an MP4 stores in its
`avcC` / `hvcC` / `av1C` boxes). This module decodes the embedded
parameter sets to the coded picture resolution, which Phase C uses to
label each track for the per-target "auto" rendition selection.

Bit-level layouts follow the published specs:

- H.264 / `avc1`: AVCDecoderConfigurationRecord (ISO/IEC 14496-15
  §5.3.3.1) wrapping SPS; SPS syntax ITU-T H.264 §7.3.2.1.1.
- HEVC / `hvc1`: HEVCDecoderConfigurationRecord (ISO/IEC 14496-15
  §8.3.3.1) wrapping VPS/SPS/PPS arrays; SPS syntax ITU-T H.265
  §7.3.2.2.1, profile_tier_level §7.3.3.
- AV1 / `av01`: AV1CodecConfigurationRecord (AV1-ISOBMFF §2.3.3)
  wrapping the sequence-header OBU; OBU syntax AV1 bitstream spec
  §5.5.1.

`parse_resolution()` never raises on malformed input — it returns
None. A track whose resolution can't be read is still routable (the
codec FourCC alone gates compatibility); resolution is a UI nicety,
not a correctness input, so a parse miss must degrade gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# H.264 profile_idc values that carry the chroma/bit-depth extension
# block in the SPS (ITU-T H.264 §7.3.2.1.1). The extension shifts every
# later field, so the set must be exact.
_AVC_HIGH_PROFILES: Final[frozenset[int]] = frozenset(
    {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}
)

_NAL_UNIT_TYPE_SPS_HEVC: Final[int] = 33


@dataclass(frozen=True, slots=True)
class Resolution:
    width: int
    height: int


class _BitReader:
    """MSB-first bit reader over a bytes buffer with Exp-Golomb support.

    Raises `_BitReaderExhaustedError` when a read runs past the end so callers
    can convert it to a graceful None rather than an IndexError leak.
    """

    __slots__ = ("_data", "_bitpos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._bitpos = 0

    def read_bit(self) -> int:
        byte_index = self._bitpos >> 3
        if byte_index >= len(self._data):
            raise _BitReaderExhaustedError
        bit_offset = 7 - (self._bitpos & 7)
        self._bitpos += 1
        return (self._data[byte_index] >> bit_offset) & 1

    def read_bits(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | self.read_bit()
        return value

    def read_ue(self) -> int:
        """Unsigned Exp-Golomb (ue(v))."""
        leading_zeros = 0
        while self.read_bit() == 0:
            leading_zeros += 1
            if leading_zeros > 31:
                raise _BitReaderExhaustedError  # malformed / runaway
        if leading_zeros == 0:
            return 0
        return (1 << leading_zeros) - 1 + self.read_bits(leading_zeros)

    def read_se(self) -> int:
        """Signed Exp-Golomb (se(v))."""
        k = self.read_ue()
        if k == 0:
            return 0
        sign = 1 if (k & 1) else -1
        return sign * ((k + 1) >> 1)

    def skip_bits(self, n: int) -> None:
        # Bounds-checked via read_bit so an over-skip raises rather than
        # silently advancing past the buffer.
        for _ in range(n):
            self.read_bit()


class _BitReaderExhaustedError(Exception):
    """Internal: a bit read ran past the buffer end."""


def _strip_emulation_prevention(data: bytes) -> bytes:
    """Remove H.264/HEVC emulation-prevention bytes (00 00 03 → 00 00).

    The NAL byte stream inserts a 0x03 after any 00 00 that would
    otherwise look like a start code. Exp-Golomb parsing must see the
    original bytes, so we strip the 0x03 that follows a 00 00 pair.
    """
    out = bytearray()
    zeros = 0
    for b in data:
        if zeros >= 2 and b == 0x03:
            zeros = 0
            continue  # drop the emulation-prevention byte
        out.append(b)
        zeros = zeros + 1 if b == 0x00 else 0
    return bytes(out)


# ---------------------------------------------------------------------------
# H.264 / avc1


def parse_avc_resolution(config_record: bytes) -> Resolution | None:
    """AVCDecoderConfigurationRecord → coded resolution, or None."""
    try:
        # Header: configurationVersion(8) profile(8) compat(8) level(8)
        # 0xFC|lengthSizeMinusOne, 0xE0|numOfSPS.
        if len(config_record) < 7:
            return None
        num_sps = config_record[5] & 0x1F
        if num_sps == 0:
            return None
        offset = 6
        sps_len = (config_record[offset] << 8) | config_record[offset + 1]
        offset += 2
        sps = config_record[offset : offset + sps_len]
        if len(sps) < 1:
            return None
        # sps[0] is the NAL header (0x67); seq_parameter_set_data follows.
        rbsp = _strip_emulation_prevention(sps[1:])
        return _parse_avc_sps(rbsp)
    except (_BitReaderExhaustedError, IndexError):
        return None


def _parse_avc_sps(rbsp: bytes) -> Resolution | None:
    r = _BitReader(rbsp)
    profile_idc = r.read_bits(8)
    r.skip_bits(8)  # constraint_set flags + reserved
    r.skip_bits(8)  # level_idc
    r.read_ue()  # seq_parameter_set_id

    chroma_format_idc = 1  # default 4:2:0 when the extension block is absent
    if profile_idc in _AVC_HIGH_PROFILES:
        chroma_format_idc = r.read_ue()
        if chroma_format_idc == 3:
            r.skip_bits(1)  # separate_colour_plane_flag
        r.read_ue()  # bit_depth_luma_minus8
        r.read_ue()  # bit_depth_chroma_minus8
        r.skip_bits(1)  # qpprime_y_zero_transform_bypass_flag
        if r.read_bit() == 1:  # seq_scaling_matrix_present_flag
            num_lists = 8 if chroma_format_idc != 3 else 12
            for i in range(num_lists):
                if r.read_bit() == 1:  # scaling_list_present
                    _skip_scaling_list(r, 16 if i < 6 else 64)

    r.read_ue()  # log2_max_frame_num_minus4
    pic_order_cnt_type = r.read_ue()
    if pic_order_cnt_type == 0:
        r.read_ue()  # log2_max_pic_order_cnt_lsb_minus4
    elif pic_order_cnt_type == 1:
        r.skip_bits(1)  # delta_pic_order_always_zero_flag
        r.read_se()  # offset_for_non_ref_pic
        r.read_se()  # offset_for_top_to_bottom_field
        # Cap at the spec maximum (ITU-T H.264 Table A-1) so a crafted
        # ue(v) of ~2 billion can't drive `range()` into a CPU spin even
        # if a future refactor stops the bit reader from exhausting first.
        num_ref_frames_in_poc_cycle = min(r.read_ue(), 255)
        for _ in range(num_ref_frames_in_poc_cycle):
            r.read_se()

    r.read_ue()  # max_num_ref_frames
    r.skip_bits(1)  # gaps_in_frame_num_value_allowed_flag
    pic_width_in_mbs_minus1 = r.read_ue()
    pic_height_in_map_units_minus1 = r.read_ue()
    frame_mbs_only_flag = r.read_bit()
    if frame_mbs_only_flag == 0:
        r.skip_bits(1)  # mb_adaptive_frame_field_flag
    r.skip_bits(1)  # direct_8x8_inference_flag

    crop_left = crop_right = crop_top = crop_bottom = 0
    if r.read_bit() == 1:  # frame_cropping_flag
        crop_left = r.read_ue()
        crop_right = r.read_ue()
        crop_top = r.read_ue()
        crop_bottom = r.read_ue()

    width = (pic_width_in_mbs_minus1 + 1) * 16
    height = (2 - frame_mbs_only_flag) * (pic_height_in_map_units_minus1 + 1) * 16

    # Crop units depend on chroma subsampling (ITU-T H.264 eq. 7-19..7-22).
    if chroma_format_idc == 0:  # monochrome
        sub_width_c, sub_height_c = 1, 1
    elif chroma_format_idc == 1:  # 4:2:0
        sub_width_c, sub_height_c = 2, 2
    elif chroma_format_idc == 2:  # 4:2:2
        sub_width_c, sub_height_c = 2, 1
    else:  # 4:4:4
        sub_width_c, sub_height_c = 1, 1
    crop_unit_x = sub_width_c
    crop_unit_y = sub_height_c * (2 - frame_mbs_only_flag)

    width -= (crop_left + crop_right) * crop_unit_x
    height -= (crop_top + crop_bottom) * crop_unit_y
    if width <= 0 or height <= 0:
        return None
    return Resolution(width=width, height=height)


def _skip_scaling_list(r: _BitReader, size: int) -> None:
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            delta = r.read_se()
            next_scale = (last_scale + delta + 256) % 256
        if next_scale != 0:
            last_scale = next_scale


# ---------------------------------------------------------------------------
# HEVC / hvc1


def parse_hevc_resolution(config_record: bytes) -> Resolution | None:
    """HEVCDecoderConfigurationRecord → coded resolution, or None."""
    try:
        if len(config_record) < 23:
            return None
        num_arrays = config_record[22]
        offset = 23
        for _ in range(num_arrays):
            if offset + 3 > len(config_record):
                return None
            nal_unit_type = config_record[offset] & 0x3F
            num_nalus = (config_record[offset + 1] << 8) | config_record[offset + 2]
            offset += 3
            for _ in range(num_nalus):
                if offset + 2 > len(config_record):
                    return None
                nal_len = (config_record[offset] << 8) | config_record[offset + 1]
                offset += 2
                nal = config_record[offset : offset + nal_len]
                offset += nal_len
                if nal_unit_type == _NAL_UNIT_TYPE_SPS_HEVC and len(nal) > 2:
                    # nal[0:2] is the 2-byte HEVC NAL header.
                    rbsp = _strip_emulation_prevention(nal[2:])
                    return _parse_hevc_sps(rbsp)
        return None
    except (_BitReaderExhaustedError, IndexError):
        return None


def _parse_hevc_sps(rbsp: bytes) -> Resolution | None:
    r = _BitReader(rbsp)
    r.skip_bits(4)  # sps_video_parameter_set_id
    max_sub_layers_minus1 = r.read_bits(3)
    r.skip_bits(1)  # sps_temporal_id_nesting_flag
    _skip_hevc_profile_tier_level(r, max_sub_layers_minus1)
    r.read_ue()  # sps_seq_parameter_set_id
    chroma_format_idc = r.read_ue()
    if chroma_format_idc == 3:
        r.skip_bits(1)  # separate_colour_plane_flag
    pic_width = r.read_ue()  # pic_width_in_luma_samples
    pic_height = r.read_ue()  # pic_height_in_luma_samples

    conf_left = conf_right = conf_top = conf_bottom = 0
    if r.read_bit() == 1:  # conformance_window_flag
        conf_left = r.read_ue()
        conf_right = r.read_ue()
        conf_top = r.read_ue()
        conf_bottom = r.read_ue()

    # SubWidthC / SubHeightC per chroma_format_idc (H.265 Table 6-1).
    if chroma_format_idc == 1:  # 4:2:0
        sub_width_c, sub_height_c = 2, 2
    elif chroma_format_idc == 2:  # 4:2:2
        sub_width_c, sub_height_c = 2, 1
    else:  # monochrome (0) or 4:4:4 (3)
        sub_width_c, sub_height_c = 1, 1

    width = pic_width - (conf_left + conf_right) * sub_width_c
    height = pic_height - (conf_top + conf_bottom) * sub_height_c
    if width <= 0 or height <= 0:
        return None
    return Resolution(width=width, height=height)


def _skip_hevc_profile_tier_level(r: _BitReader, max_sub_layers_minus1: int) -> None:
    # ITU-T H.265 §7.3.3 profile_tier_level(profilePresentFlag=1, maxNumSubLayersMinus1).
    # General PTL = 96 bits, in four skips below:
    #   8 bits  — profile_space[2] tier_flag[1] profile_idc[5]
    #   32 bits — general_profile_compatibility_flag[32]
    #   48 bits — four source flags, 43 constraint bits, inbld/reserved bit
    #   8 bits  — general_level_idc
    r.skip_bits(8)
    r.skip_bits(32)
    r.skip_bits(48)
    r.skip_bits(8)

    if max_sub_layers_minus1 == 0:
        return

    sub_layer_profile_present = []
    sub_layer_level_present = []
    for _ in range(max_sub_layers_minus1):
        sub_layer_profile_present.append(r.read_bit())
        sub_layer_level_present.append(r.read_bit())
    # reserved_zero_2bits alignment for layers [max_sub_layers_minus1, 8).
    for _ in range(max_sub_layers_minus1, 8):
        r.skip_bits(2)
    for i in range(max_sub_layers_minus1):
        # Sub-layer profile block = general profile block minus the 8-bit
        # level: 8 + 32 + 48 = 88 bits. Sub-layer level = 8 bits.
        if sub_layer_profile_present[i]:
            r.skip_bits(88)
        if sub_layer_level_present[i]:
            r.skip_bits(8)


# ---------------------------------------------------------------------------
# AV1 / av01


def parse_av1_resolution(config_record: bytes) -> Resolution | None:
    """AV1CodecConfigurationRecord → max coded resolution, or None."""
    try:
        # Header is 4 bytes (marker/version, seq_profile/level, flags,
        # delay); configOBUs follow.
        if len(config_record) < 5:
            return None
        obus = config_record[4:]
        seq_header = _find_av1_sequence_header_obu(obus)
        if seq_header is None:
            return None
        return _parse_av1_sequence_header(seq_header)
    except (_BitReaderExhaustedError, IndexError):
        return None


def _find_av1_sequence_header_obu(data: bytes) -> bytes | None:
    """Walk the OBU stream and return the OBU_SEQUENCE_HEADER payload."""
    offset = 0
    while offset < len(data):
        header = data[offset]
        offset += 1
        obu_type = (header >> 3) & 0x0F
        obu_extension_flag = (header >> 2) & 1
        obu_has_size_field = (header >> 1) & 1
        if obu_extension_flag:
            offset += 1  # temporal/spatial id byte
        if obu_has_size_field:
            obu_size, offset = _read_leb128(data, offset)
        else:
            obu_size = len(data) - offset
        payload = data[offset : offset + obu_size]
        offset += obu_size
        if obu_type == 1:  # OBU_SEQUENCE_HEADER
            return payload
    return None


def _read_leb128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for i in range(8):
        if offset >= len(data):
            raise _BitReaderExhaustedError
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << (i * 7)
        if not (byte & 0x80):
            break
    return value, offset


def _parse_av1_sequence_header(payload: bytes) -> Resolution | None:
    r = _BitReader(payload)
    r.skip_bits(3)  # seq_profile
    r.skip_bits(1)  # still_picture
    reduced_still_picture_header = r.read_bit()

    if reduced_still_picture_header:
        r.skip_bits(5)  # seq_level_idx[0]
    else:
        timing_info_present_flag = r.read_bit()
        if timing_info_present_flag:
            # timing_info(): num_units_in_display_tick(32), time_scale(32),
            # equal_picture_interval(1) + maybe uvlc; decoder_model_info...
            # OBS/x264/aom default streams don't set this — bail rather than
            # decode the variable-length tail.
            return None
        initial_display_delay_present_flag = r.read_bit()
        operating_points_cnt_minus_1 = r.read_bits(5)
        for _ in range(operating_points_cnt_minus_1 + 1):
            r.skip_bits(12)  # operating_point_idc[i]
            seq_level_idx = r.read_bits(5)
            if seq_level_idx > 7:
                r.skip_bits(1)  # seq_tier[i]
            if initial_display_delay_present_flag and r.read_bit():
                r.skip_bits(4)  # initial_display_delay_minus_1[i]

    frame_width_bits_minus_1 = r.read_bits(4)
    frame_height_bits_minus_1 = r.read_bits(4)
    max_frame_width_minus_1 = r.read_bits(frame_width_bits_minus_1 + 1)
    max_frame_height_minus_1 = r.read_bits(frame_height_bits_minus_1 + 1)
    return Resolution(width=max_frame_width_minus_1 + 1, height=max_frame_height_minus_1 + 1)


# ---------------------------------------------------------------------------
# Dispatcher


def parse_resolution(codec_fourcc: str, seq_start_payload: bytes) -> Resolution | None:
    """Decode a video SEQ_START payload to its coded resolution.

    `codec_fourcc` is the lowercase FourCC from the E-RTMP tag
    (`avc1` / `hvc1` / `av01`). Returns None for unknown codecs or any
    parse failure — resolution is a label, never a routing input.
    """
    if codec_fourcc == "avc1":
        return parse_avc_resolution(seq_start_payload)
    if codec_fourcc == "hvc1":
        return parse_hevc_resolution(seq_start_payload)
    if codec_fourcc == "av01":
        return parse_av1_resolution(seq_start_payload)
    return None
