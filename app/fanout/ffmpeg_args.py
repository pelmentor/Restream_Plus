"""ffmpeg argv construction per ADR-0008 §"Server-computed args".

`FFMPEG_BASE_ARGS` is the locked, server-computed flag set used for
EVERY target type. Per-type overrides live in `FFMPEG_BASE_ARGS_BY_TYPE`
(empty in v1 — every platform accepts `-c copy -f flv`); a future need
slots in here, never in user-facing config (F# SA-E.locked-args).

`build_argv(spec)` composes the final argv. The output URL goes last
so a future flag insertion cannot accidentally end up positional after
the URL.
"""

from __future__ import annotations

from typing import Final

from app.domain.target_types import TargetType
from app.fanout.worker import WorkerSpec

FFMPEG_BASE_ARGS: Final[tuple[str, ...]] = (
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostats",
    "-progress",
    "pipe:1",
    "-rtmp_buffer",
    "100",
    "-fflags",
    "+nobuffer",
    "-flags",
    "low_delay",
    "-f",
    "flv",
    "-flvflags",
    "no_duration_filesize",
    "-flush_packets",
    "1",
    # `-c copy` lives after the input flag set in build_argv() because
    # ffmpeg parses codec flags relative to the most recently named
    # stream. Putting it after `-i <input>` ensures it applies to the
    # output, not the input.
)
"""ADR-0008's locked baseline — read top-to-bottom as the spec.

What each flag buys us:
- `-hide_banner -loglevel error -nostats`: keep stderr small (only
  errors), so the redaction sink has less work and the ring buffer
  isn't drowned by version banners.
- `-progress pipe:1`: structured progress on stdout (consumed by
  `ProgressParser`).
- `-rtmp_buffer 100 -fflags +nobuffer -flags low_delay -flush_packets 1`:
  collectively, "pull as fast as the stream comes in, never pre-buffer,
  push every packet to the output as it arrives" — minimum added
  latency.
- `-f flv -flvflags no_duration_filesize`: mark the FLV container as
  a live stream (some platforms reject FLVs that pretend to know their
  total duration upfront).
"""

FFMPEG_BASE_ARGS_BY_TYPE: Final[dict[TargetType, tuple[str, ...]]] = {}
"""Per-type overrides. Empty in v1; reserved for future quirks.

If, e.g., VK ever needs `-vk_thing 1`, it lands here keyed by
`TargetType.VK_LIVE`. NEVER in user config — that would resurrect the
no-transcoding rule as a user-violatable convention."""


def build_argv(spec: WorkerSpec) -> list[str]:
    """Compose the final argv for `asyncio.create_subprocess_exec`.

    Order:
      1. `FFMPEG_BASE_ARGS` (input-side flags).
      2. `-i <loopback_url>` — pulled from the spec.
      3. `-c copy` — codec flag for the output stream (after `-i`).
      4. `FFMPEG_BASE_ARGS_BY_TYPE[type]` if any per-type overrides
         apply (empty today).
      5. `<output_url>` — the destination, embedding the credential.

    The output URL goes last; a future flag insertion can't accidentally
    become a positional after the URL.
    """
    overrides = FFMPEG_BASE_ARGS_BY_TYPE.get(spec.target_type, ())
    return [
        *FFMPEG_BASE_ARGS,
        "-i",
        spec.input_url,
        "-c",
        "copy",
        *overrides,
        spec.output_url,
    ]
