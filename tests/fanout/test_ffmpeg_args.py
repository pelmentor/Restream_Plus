"""FFMPEG_BASE_ARGS + build_argv tests."""

from __future__ import annotations

from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole
from app.fanout.ffmpeg_args import (
    FFMPEG_BASE_ARGS,
    FFMPEG_BASE_ARGS_BY_TYPE,
    build_argv,
)
from app.fanout.worker import WorkerId, WorkerSpec


def _spec(
    *,
    target_type: TargetType = TargetType.TWITCH,
    output_url: str = "rtmp://live.twitch.tv/app/test_key_12345",
) -> WorkerSpec:
    return WorkerSpec(
        worker_id=WorkerId(target_id="t1", role=WorkerRole.PRIMARY),
        target_type=target_type,
        input_url="rtmp://127.0.0.1:1935/live/ingest123",
        output_url=output_url,
    )


class TestBaseArgs:
    def test_starts_with_ffmpeg(self) -> None:
        assert FFMPEG_BASE_ARGS[0] == "ffmpeg"

    def test_progress_pipe_present(self) -> None:
        # Required for ProgressParser to work.
        assert "-progress" in FFMPEG_BASE_ARGS
        i = FFMPEG_BASE_ARGS.index("-progress")
        assert FFMPEG_BASE_ARGS[i + 1] == "pipe:1"

    def test_loglevel_error(self) -> None:
        assert "-loglevel" in FFMPEG_BASE_ARGS
        i = FFMPEG_BASE_ARGS.index("-loglevel")
        assert FFMPEG_BASE_ARGS[i + 1] == "error"

    def test_low_latency_flags_present(self) -> None:
        assert "-fflags" in FFMPEG_BASE_ARGS
        i = FFMPEG_BASE_ARGS.index("-fflags")
        assert FFMPEG_BASE_ARGS[i + 1] == "+nobuffer"
        assert "-flags" in FFMPEG_BASE_ARGS
        i = FFMPEG_BASE_ARGS.index("-flags")
        assert FFMPEG_BASE_ARGS[i + 1] == "low_delay"

    def test_flv_format(self) -> None:
        assert "-f" in FFMPEG_BASE_ARGS
        i = FFMPEG_BASE_ARGS.index("-f")
        assert FFMPEG_BASE_ARGS[i + 1] == "flv"

    def test_no_user_settable_codec_flags_in_base(self) -> None:
        # `-c copy` is appended in build_argv after `-i`, not in base.
        assert "-c" not in FFMPEG_BASE_ARGS

    def test_no_per_type_overrides_in_v1(self) -> None:
        assert FFMPEG_BASE_ARGS_BY_TYPE == {}


class TestBuildArgv:
    def test_argv_ends_with_output_url(self) -> None:
        spec = _spec(output_url="rtmp://live.twitch.tv/app/test_key_12345")
        argv = build_argv(spec)
        assert argv[-1] == "rtmp://live.twitch.tv/app/test_key_12345"

    def test_argv_includes_input(self) -> None:
        spec = _spec()
        argv = build_argv(spec)
        assert "-i" in argv
        i = argv.index("-i")
        assert argv[i + 1] == "rtmp://127.0.0.1:1935/live/ingest123"

    def test_codec_flag_is_copy_after_input(self) -> None:
        spec = _spec()
        argv = build_argv(spec)
        i_input = argv.index("-i")
        i_copy = argv.index("-c")
        assert i_copy > i_input
        assert argv[i_copy + 1] == "copy"

    def test_output_url_is_last_positional(self) -> None:
        spec = _spec(output_url="rtmps://b.rtmps.youtube.com:443/live2/key?backup=1")
        argv = build_argv(spec)
        # Output URL must come after every flag, never before any -flag.
        assert argv[-1] == spec.output_url
        # No `-flag value` pair after the URL.
        assert all(not a.startswith("-") for a in argv[-1:])

    def test_invariant_locked_args_no_user_input_path(self) -> None:
        # Even if a user-pretended-to-be-an-arg URL got through, it
        # lands ONLY in the output position, not as a free flag.
        spec = _spec(output_url="rtmp://live.example/app/-rce_attempt")
        argv = build_argv(spec)
        # The literal "-rce_attempt" appears only as part of the URL,
        # never as its own argv entry.
        assert "-rce_attempt" not in argv
        assert argv[-1] == spec.output_url
