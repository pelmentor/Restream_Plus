"""Ground-truth decoder-config-record test vectors for sequence_header.

Generated locally with ffmpeg 7.1.1 (libx264 / libx265 / libaom-av1):

    ffmpeg -f lavfi -i color=c=black:s=<W>x<H>:d=0.1 -c:v <enc> ... out.mp4

then the `avcC` / `hvcC` / `av1C` box payload was extracted. CI has no
ffmpeg, so these bytes are pinned here as the authoritative fixtures —
the same byte sequences OBS emits in the Enhanced-RTMP video
`PACKETTYPE_SEQ_START` tag (the SEQ_START payload IS the decoder config
record).

The HEVC records are minimised: the real x265 output embeds a ~2.4 KB
SEI options string in a trailing NAL array, irrelevant to resolution.
These records keep the genuine ffmpeg header + the unmodified SPS NAL
array, with the SEI array dropped and numOfArrays set to 1.

Each entry: `(codec_fourcc, hex, (width, height))`.
"""

from __future__ import annotations

# H.264 / avc1 — AVCDecoderConfigurationRecord
AVC_1080 = "01640028ffe1001b67640028acd940780227e5c044000003000400000300c83c60c65801000668ebe3cb22c0fdf8f800"  # noqa: E501
AVC_720 = (
    "0164001fffe1001a6764001facd9405005bb011000000300100000030320f183196001000668ebe3cb22c0fdf8f800"
)
AVC_480 = (
    "0164001effe1001a6764001eacd940d83de6f011000003000100000300320f162d9601000668ebe3cb22c0fdf8f800"
)

# HEVC / hvc1 — HEVCDecoderConfigurationRecord (SPS-array only, SEI dropped)
HEVC_1080 = "01016000000090000000000078f000fcfdf8f800000f01a10001002a420101016000000300900000030000030078a003c08010e596566924caf0168080000003008000000c84"  # noqa: E501
HEVC_720 = "0101600000009000000000005df000fcfdf8f800000f01a10001002b42010101600000030090000003000003005da00280802d165959a4932bc05a020000030002000003003210"  # noqa: E501
HEVC_480 = "0101600000009000000000005af000fcfdf8f800000f01a10001002a42010101600000030090000003000003005aa006b201e1d796566924caf0168080000003008000000c84"  # noqa: E501

# AV1 / av01 — AV1CodecConfigurationRecord
AV1_1080 = "81080c000a0b00000042abbfc3732be401"
AV1_720 = "81050c000a0b0000002d4cffb3ccaf9004"
AV1_480 = "81040c000a0b00000024c6abdf36be4010"


VECTORS: tuple[tuple[str, str, tuple[int, int]], ...] = (
    ("avc1", AVC_1080, (1920, 1080)),
    ("avc1", AVC_720, (1280, 720)),
    ("avc1", AVC_480, (854, 480)),
    ("hvc1", HEVC_1080, (1920, 1080)),
    ("hvc1", HEVC_720, (1280, 720)),
    ("hvc1", HEVC_480, (854, 480)),
    ("av01", AV1_1080, (1920, 1080)),
    ("av01", AV1_720, (1280, 720)),
    ("av01", AV1_480, (854, 480)),
)
