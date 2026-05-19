"""Phase A pilot CLI — classify FLV tags in a captured OBS push.

Thin wrapper around `app.fanout.flv.parser`. The parser itself lives
in the production package so the supervisor can reuse it at Phase C
without redirecting through `scripts/`.

Reads a `capture.flv` produced by `ffmpeg -i rtmp://... -c copy -f flv capture.flv`
during the multi-track pilot (see `docs/ops/multitrack-pilot.md`) and
prints a per-flavor / per-track summary.

Run:
    python scripts/pilot_ertmp.py path/to/capture.flv
    python scripts/pilot_ertmp.py path/to/capture.flv --verbose
    python scripts/pilot_ertmp.py path/to/capture.flv --json

Exit codes:
    0 = parse successful (whatever the multi-track outcome).
    1 = usage error (missing file).
    2 = parse error (malformed FLV / truncated / unrecognised tag type).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import BinaryIO

from app.fanout.flv.parser import (
    AnalysisReport,
    AudioTagInfo,
    FlvParseError,
    VideoTagInfo,
    analyse,
    parse_flv,
)


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
