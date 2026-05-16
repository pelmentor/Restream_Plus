"""Per-target-type output URL composition (platforms-reference.md).

The supervisor calls `compose_out_url(target_type, target_url, key,
role)` to produce the URL the FFmpeg worker pushes to. Per-type
shapes:

- **Twitch** (`rtmp://live.twitch.tv/app`): `<url>/<key>`.
- **YouTube primary** (`rtmps://a.rtmps.youtube.com:443/live2`):
  `<url>/<key>`.
- **YouTube backup** (`rtmps://b.rtmps.youtube.com:443/live2?backup=1`):
  derived from primary by host swap (a → b) and tail `?backup=1`. The
  caller does NOT supply a separate backup URL; we compose it from the
  primary URL because the user only configures one URL per target.
- **Kick** (`rtmps://<ivs>:443/app`): `<url>/<key>`.
- **VK Live** (`rtmp://ovsu.okcdn.ru/input/`): `<url><key>` — the
  default URL ends with `/`, so we don't add another (the user's URL
  may or may not have a trailing slash; we strip-and-rejoin to
  normalize).
- **Custom** — `<url>/<key>` if `key` is non-empty, else `<url>`
  unchanged. Lets users push to endpoints whose key is encoded in the
  URL itself (e.g., a query string).

The function NEVER logs the resulting URL (it contains the plaintext
key). The supervisor registers the key in `CredentialRegistry` BEFORE
calling this so any incidental log site is still redacted.
"""

from __future__ import annotations

from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole

_YOUTUBE_BACKUP_HOST_PREFIX = "a.rtmps."
_YOUTUBE_BACKUP_HOST_REPLACE = "b.rtmps."
_YOUTUBE_BACKUP_QUERY = "?backup=1"


def compose_out_url(
    *,
    target_type: TargetType,
    target_url: str,
    plaintext_key: str,
    role: WorkerRole,
) -> str:
    """Compose the full output URL ffmpeg should push to.

    Raises:
        ValueError: if `role is BACKUP` and `target_type is not YOUTUBE`
            (no other platform supports a backup ingest in v1), or if
            the inputs would produce an empty URL, or if `target_url`
            does not start with `rtmp://` / `rtmps://` (a leading `-`
            from a malicious Custom-target URL would otherwise be
            interpreted by ffmpeg as a flag rather than an output URL).
    """
    if not target_url:
        raise ValueError("target_url must not be empty")
    if not target_url.startswith(("rtmp://", "rtmps://")):
        raise ValueError(
            "target_url must use rtmp:// or rtmps:// scheme; "
            "ffmpeg argv injection guard (post-Phase-5 review fix M4)"
        )

    if role is WorkerRole.BACKUP and target_type is not TargetType.YOUTUBE:
        raise ValueError(
            f"backup role is only valid for YouTube targets; got {target_type.value!r}"
        )

    if target_type is TargetType.VK_LIVE:
        # VK's URL ends with "/input/"; key concatenates as a path
        # segment. Normalize to ensure exactly one slash separator.
        return target_url.rstrip("/") + "/" + plaintext_key

    if target_type is TargetType.CUSTOM:
        if not plaintext_key:
            return target_url
        return target_url.rstrip("/") + "/" + plaintext_key

    if target_type is TargetType.YOUTUBE and role is WorkerRole.BACKUP:
        # Compose backup URL from primary by host swap + query suffix.
        # If the user customized to a different YouTube host, swap on
        # the literal "a.rtmps." to "b.rtmps." prefix; otherwise
        # leave the host alone (best-effort).
        backup_url = target_url.replace(
            _YOUTUBE_BACKUP_HOST_PREFIX,
            _YOUTUBE_BACKUP_HOST_REPLACE,
            1,
        )
        return backup_url.rstrip("/") + "/" + plaintext_key + _YOUTUBE_BACKUP_QUERY

    # Twitch, YouTube primary, Kick — same shape: `<url>/<key>`.
    return target_url.rstrip("/") + "/" + plaintext_key
