"""compose_out_url tests — per-platform URL shapes."""

from __future__ import annotations

import pytest
from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole
from app.fanout.ffmpeg_urls import compose_out_url


class TestTwitch:
    def test_appends_key_after_app_path(self) -> None:
        url = compose_out_url(
            target_type=TargetType.TWITCH,
            target_url="rtmp://live.twitch.tv/app",
            plaintext_key="abc123XYZ",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://live.twitch.tv/app/abc123XYZ"

    def test_strips_trailing_slash(self) -> None:
        url = compose_out_url(
            target_type=TargetType.TWITCH,
            target_url="rtmp://live.twitch.tv/app/",
            plaintext_key="abc123XYZ",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://live.twitch.tv/app/abc123XYZ"


class TestYouTubePrimary:
    def test_appends_key(self) -> None:
        url = compose_out_url(
            target_type=TargetType.YOUTUBE,
            target_url="rtmps://a.rtmps.youtube.com:443/live2",
            plaintext_key="yt-key-XYZ",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmps://a.rtmps.youtube.com:443/live2/yt-key-XYZ"


class TestYouTubeBackup:
    def test_swaps_a_to_b_host_and_appends_query(self) -> None:
        url = compose_out_url(
            target_type=TargetType.YOUTUBE,
            target_url="rtmps://a.rtmps.youtube.com:443/live2",
            plaintext_key="yt-key-XYZ",
            role=WorkerRole.BACKUP,
        )
        assert url == "rtmps://b.rtmps.youtube.com:443/live2/yt-key-XYZ?backup=1"

    def test_backup_only_valid_for_youtube(self) -> None:
        with pytest.raises(ValueError, match="backup role"):
            compose_out_url(
                target_type=TargetType.TWITCH,
                target_url="rtmp://live.twitch.tv/app",
                plaintext_key="abc",
                role=WorkerRole.BACKUP,
            )


class TestKick:
    def test_appends_key(self) -> None:
        url = compose_out_url(
            target_type=TargetType.KICK,
            target_url="rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
            plaintext_key="kick-key-XYZ",
            role=WorkerRole.PRIMARY,
        )
        assert url.endswith("/app/kick-key-XYZ")


class TestVKLive:
    def test_appends_key_with_normalized_slash(self) -> None:
        url = compose_out_url(
            target_type=TargetType.VK_LIVE,
            target_url="rtmp://ovsu.okcdn.ru/input/",
            plaintext_key="vk-fresh-key",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://ovsu.okcdn.ru/input/vk-fresh-key"

    def test_handles_url_without_trailing_slash(self) -> None:
        url = compose_out_url(
            target_type=TargetType.VK_LIVE,
            target_url="rtmp://ovsu.okcdn.ru/input",
            plaintext_key="vk-fresh-key",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://ovsu.okcdn.ru/input/vk-fresh-key"


class TestCustom:
    def test_appends_key_when_present(self) -> None:
        url = compose_out_url(
            target_type=TargetType.CUSTOM,
            target_url="rtmp://my.example/stream",
            plaintext_key="my-key",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://my.example/stream/my-key"

    def test_omits_key_when_empty(self) -> None:
        # User pushed key into the URL itself; we don't double-append.
        url = compose_out_url(
            target_type=TargetType.CUSTOM,
            target_url="rtmp://my.example/stream/already-has-key",
            plaintext_key="",
            role=WorkerRole.PRIMARY,
        )
        assert url == "rtmp://my.example/stream/already-has-key"


class TestEmptyURL:
    def test_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            compose_out_url(
                target_type=TargetType.TWITCH,
                target_url="",
                plaintext_key="abc",
                role=WorkerRole.PRIMARY,
            )


class TestSchemeGuard:
    """Post-Phase-5 review M4: guard against argv injection via a
    user-supplied target URL that starts with `-`. ffmpeg would
    otherwise interpret the URL as a flag rather than an output."""

    def test_rejects_leading_dash_url(self) -> None:
        with pytest.raises(ValueError, match="rtmp:// or rtmps://"):
            compose_out_url(
                target_type=TargetType.CUSTOM,
                target_url="-loglevel debug rtmp://attacker/x",
                plaintext_key="anykey",
                role=WorkerRole.PRIMARY,
            )

    def test_rejects_http_url(self) -> None:
        with pytest.raises(ValueError, match="rtmp:// or rtmps://"):
            compose_out_url(
                target_type=TargetType.CUSTOM,
                target_url="http://example.com/stream",
                plaintext_key="anykey",
                role=WorkerRole.PRIMARY,
            )

    def test_rejects_file_url(self) -> None:
        with pytest.raises(ValueError, match="rtmp:// or rtmps://"):
            compose_out_url(
                target_type=TargetType.CUSTOM,
                target_url="file:///etc/passwd",
                plaintext_key="anykey",
                role=WorkerRole.PRIMARY,
            )
