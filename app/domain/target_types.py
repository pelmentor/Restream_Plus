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


TARGET_TYPE_SPECS: Final[dict[TargetType, TargetTypeSpec]] = {
    TargetType.TWITCH: TargetTypeSpec(
        type=TargetType.TWITCH,
        display_label="Twitch",
        default_url="rtmp://live.twitch.tv/app",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset(
            {FormField.URL, FormField.LABEL, FormField.STREAM_KEY, FormField.REGION},
        ),
        notes="H.264 only, up to 6000 kbps video, 2 s keyframe interval.",
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
    ),
    TargetType.KICK: TargetTypeSpec(
        type=TargetType.KICK,
        display_label="Kick",
        default_url="rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset({FormField.URL, FormField.LABEL, FormField.STREAM_KEY}),
        notes="Hosted on AWS IVS; the unbranded hostname is the official endpoint.",
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
    ),
    TargetType.CUSTOM: TargetTypeSpec(
        type=TargetType.CUSTOM,
        display_label="Custom RTMP",
        default_url="",
        backup_supported=False,
        credential_lifetime=CredentialLifetime.PERSISTENT,
        settings_fields=frozenset({FormField.URL, FormField.LABEL, FormField.STREAM_KEY}),
        notes="Any RTMP/RTMPS endpoint. URL and key are your responsibility.",
    ),
}


def spec_for(target_type: TargetType) -> TargetTypeSpec:
    return TARGET_TYPE_SPECS[target_type]
