"""Per-target-type defaults — the static catalogue of platforms.

Single source of truth for default URLs, supported features (backup ingest),
credential lifetime, and the form-field set the Settings UI should render.
Values come from `docs/architecture/platforms-reference.md`; changes there
must mirror here and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from app.domain.credential_policy import CredentialLifetime


class TargetType(str, Enum):
    TWITCH = "twitch"
    YOUTUBE = "youtube"
    KICK = "kick"
    VK_LIVE = "vk_live"
    CUSTOM = "custom"


class FormField(str, Enum):
    """Fields the Settings form may render for a target type.

    The set is per-type; Phase 9 (frontend Settings) consumes it to
    decide which controls to draw. VK explicitly omits `STREAM_KEY`
    (the Dashboard's per-session prompt collects it instead).
    """

    URL = "url"
    LABEL = "label"
    STREAM_KEY = "stream_key"
    BACKUP = "backup"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class TargetTypeSpec:
    """Static config for one target type. Frozen — never mutated at runtime."""

    type: TargetType
    display_label: str
    default_url: str
    backup_supported: bool
    credential_lifetime: CredentialLifetime
    settings_fields: frozenset[FormField]
    notes: str
    accepted_video_codecs: frozenset[str]
    """Video FourCCs (lowercase ASCII) the platform's ingest will accept.

    Populated per ADR-0016 §6 from each platform's published RTMP ingest spec
    plus OBS's `reference/obs-studio/plugins/rtmp-services/data/services.json`
    capability declarations. Phase C uses this to gate per-target multi-track
    selection: a target whose `track_preference` resolves to a codec that
    isn't in this set transitions to `DISABLED_MISCONFIGURED` (fail-loud) —
    we do NOT transcode (ADR-0008 `-c copy` lock).

    `frozenset()` means "no codec capability declared" — used by
    `TargetType.CUSTOM` where the operator owns the endpoint and its
    constraints. Runtime behavior for an empty set is fail-loud at the
    config layer: a multi-track stream cannot route to a CUSTOM target
    without the operator explicitly declaring the accepted codec(s).
    """


TARGET_TYPE_SPECS: Final[dict[TargetType, TargetTypeSpec]] = {
    TargetType.TWITCH: TargetTypeSpec(
        type=TargetType.TWITCH,
        display_label="Twitch",
        # `live.twitch.tv/app` is Twitch's "intelligent ingest" hostname
        # that historically smart-routes to the nearest regional server.
        # It is NOT in OBS's bundled `services.json` — they only ship the
        # 46 regional URLs. The smart-routing default fails for some
        # operators (the regional list lives in `targetTypeSpecs.ts` so
        # operators can pick a specific region when the default fails).
        # When we ship the OBS Auto Stream Configuration adapter we'll
        # route through `https://ingest.twitch.tv/api/v3/GetClientConfiguration`
        # to negotiate the best regional URL automatically.
        default_url="rtmp://live.twitch.tv/app",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset(
            {FormField.URL, FormField.LABEL, FormField.STREAM_KEY, FormField.REGION},
        ),
        notes="H.264 only, up to 6000 kbps video, 2 s keyframe interval.",
        # Standard Twitch ingest is H.264-only per
        # reference/obs-studio/plugins/rtmp-services/data/services.json:204-206.
        # Twitch Multitrack (the proprietary GetClientConfiguration API,
        # RTX-only HEVC beta) is explicitly out of scope per ADR-0016 §5.
        accepted_video_codecs=frozenset({"avc1"}),
    ),
    TargetType.YOUTUBE: TargetTypeSpec(
        type=TargetType.YOUTUBE,
        display_label="YouTube Live",
        default_url="rtmps://a.rtmps.youtube.com:443/live2",
        backup_supported=True,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset(
            {FormField.URL, FormField.LABEL, FormField.STREAM_KEY, FormField.BACKUP},
        ),
        notes=(
            "Backup ingest at rtmps://b.rtmps.youtube.com:443/live2 with the same key. "
            "When enabled we run two workers concurrently (hot failover)."
        ),
        # YouTube RTMPS accepts H.264, HEVC, AV1 per
        # reference/obs-studio/plugins/rtmp-services/data/services.json:244-248.
        # AV1 is YouTube-only among our supported platforms today.
        accepted_video_codecs=frozenset({"avc1", "hvc1", "av01"}),
    ),
    TargetType.KICK: TargetTypeSpec(
        type=TargetType.KICK,
        display_label="Kick",
        default_url="rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset({FormField.URL, FormField.LABEL, FormField.STREAM_KEY}),
        notes="Hosted on AWS IVS; the unbranded hostname is the official endpoint.",
        # Kick / AWS IVS standard ingest is H.264-only. No public docs
        # advertise HEVC/AV1 ingest capability as of 2026-05.
        accepted_video_codecs=frozenset({"avc1"}),
    ),
    TargetType.VK_LIVE: TargetTypeSpec(
        type=TargetType.VK_LIVE,
        display_label="VK Video Live",
        default_url="rtmp://ovsu.okcdn.ru/input/",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PER_SESSION,
        settings_fields=frozenset({FormField.URL, FormField.LABEL}),
        notes=(
            "VK requires a fresh stream key per broadcast. "
            "Paste it on the Dashboard before pressing START."
        ),
        # VK Live ingest is H.264-only per published docs.
        accepted_video_codecs=frozenset({"avc1"}),
    ),
    TargetType.CUSTOM: TargetTypeSpec(
        type=TargetType.CUSTOM,
        display_label="Custom RTMP",
        default_url="",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset({FormField.URL, FormField.LABEL, FormField.STREAM_KEY}),
        notes="Any RTMP/RTMPS endpoint. URL and key are your responsibility.",
        # Empty set — Custom RTMP has no declared capability. Phase C
        # fail-loud: a multi-track source cannot route to a CUSTOM target
        # without the operator explicitly declaring accepted codec(s).
        # Until that declaration UI exists, single-track-only is the
        # safe default for Custom.
        accepted_video_codecs=frozenset(),
    ),
}


def spec_for(target_type: TargetType) -> TargetTypeSpec:
    return TARGET_TYPE_SPECS[target_type]
