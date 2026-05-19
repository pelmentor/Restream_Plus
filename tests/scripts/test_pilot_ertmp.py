"""CLI smoke tests for `scripts/pilot_ertmp.py`.

Parsing semantics are covered exhaustively in
`tests/fanout/flv/test_parser.py`. This module only tests the
argparse + I/O surface so a refactor that breaks the CLI behaviour
fails CI even when the parser tests stay green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.pilot_ertmp import main

from tests.fanout.flv.test_parser import (
    audio_legacy_body,
    flv_tag,
    make_flv,
    video_legacy_body,
    video_multitrack_one_track_body,
)


def test_cli_human_summary_on_valid_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capture = tmp_path / "capture.flv"
    capture.write_bytes(
        make_flv(
            [
                flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0)),
                flv_tag(9, video_multitrack_one_track_body(fourcc=b"hvc1", track_id=1)),
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
        make_flv([flv_tag(9, video_multitrack_one_track_body(fourcc=b"avc1", track_id=0))])
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
    capture.write_bytes(
        make_flv([flv_tag(9, video_legacy_body()), flv_tag(8, audio_legacy_body())])
    )
    rc = main([str(capture), "--verbose"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "video flavor=legacy" in out
    assert "audio flavor=legacy" in out


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
