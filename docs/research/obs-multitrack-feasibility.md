# OBS Multitrack Video over Custom RTMP — feasibility study

**Date:** 2026-05-19
**Status:** Research / verdict overrides ADR-0003 §"Open questions"
**Source repo:** `reference/obs-studio/` (shallow clone of
[obsproject/obs-studio](https://github.com/obsproject/obs-studio), `--depth 1
--filter=blob:none`; the `/reference/` directory is gitignored)

## TL;DR

Multitrack Video against a Custom RTMP service **is supported by OBS** —
the previous "deferred until upstream catches up" conclusion (ADR-0003
§"Open questions", 2026-05-18) was wrong. It missed the
`custom_config_only` escape hatch that OBS devs added precisely for
custom-server users:

> When `service.id == "rtmp_custom"` AND the user supplies a
> `multitrack_video_config_override` JSON in the settings UI, OBS skips
> the remote configuration POST (`multitrack_video_configuration_url`)
> entirely and uses the user-supplied JSON as the encoder ladder.

The wire format is the **Veovera Enhanced RTMP Y2023 spec** (E-RTMP);
nginx-rtmp / MediaMTX pass the binary tags through opaquely; the work
on Restream_Plus's side is server-side: an FLV demuxer that understands
E-RTMP `PACKETTYPE_MULTITRACK` tags plus a track-aware fanout layer.

This document captures the source-level evidence so future work can
start from facts rather than re-running the black-box test.

## 1. The UI flow — where the checkbox lives, when it's enabled

The "Enable Multitrack Video" group is rendered in the Stream settings
panel. Its visibility is determined by
[OBSBasicSettings.cpp:5597-5610](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5597):

```cpp
auto available = protocol.startsWith("RTMP");

if (available && !IsCustomService()) {
    OBSServiceAutoRelease temp_service =
        obs_service_create_private("rtmp_common",
                                   "auto config query service",
                                   settings);
    settings = obs_service_get_settings(temp_service);
    available = obs_data_has_user_value(
        settings, "multitrack_video_configuration_url");
    if (!available && ui->enableMultitrackVideo->isChecked())
        ui->enableMultitrackVideo->setChecked(false);
}

ui->multitrackVideoGroupBox->setVisible(available);
```

**Key observation:** the `multitrack_video_configuration_url` lookup is
only performed when the service is NOT custom. For
`IsCustomService() == true`, the visibility check is short-circuited and
the group box is shown unconditionally (as long as protocol is RTMP).

The `multitrack_video_config_override` text field directly below the
checkbox is only shown for the custom-service branch
([OBSBasicSettings.cpp:5631-5633](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5631)):

```cpp
ui->multitrackVideoConfigOverride->setVisible(
    available && IsCustomService());
```

This is the operator-facing surface for the escape hatch.

## 2. The runtime branch — `custom_config_only` mode

When the user clicks Start, OBS routes through
[MultitrackVideoOutput::PrepareStreaming](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L126).
The branching logic lives at
[MultitrackVideoOutput.cpp:413-451](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L413):

```cpp
const bool custom_config_only =
    auto_config_url.isEmpty()
    && custom_config.has_value()
    && strcmp(obs_service_get_id(service), "rtmp_custom") == 0;

if (!custom_config_only) {
    auto go_live_post = constructGoLivePost(...);
    go_live_config = DownloadGoLiveConfig(parent, auto_config_url,
                                          go_live_post,
                                          multitrack_video_name);
}

// ... resolution / encoder selection ...

if (!go_live_config && !custom) {
    log(LOG_ERROR,
        "MultitrackVideoOutput: no config set, this should never happen");
    throw MultitrackVideoError::warning(
        QTStr("FailedToStartStream.NoConfig"));
}
```

The three branches:

| Service                       | `auto_config_url` | `custom_config` | Behaviour                                                        |
|-------------------------------|-------------------|-----------------|------------------------------------------------------------------|
| Standard (Twitch, etc.)       | Non-empty (from `services.json`) | Optional | POSTs to the URL, gets back a ladder, encodes              |
| Custom RTMP, no override JSON | Empty             | Empty           | Throws `FailedToStartStream.NoConfig` — this is what 2026-05-18 testing observed |
| Custom RTMP, with override    | Empty             | Non-empty       | Enters `custom_config_only`, skips POST, uses the JSON directly |

The 2026-05-18 empirical test ("OBS failed to start / Output failure")
landed in row 2 because the operator did not paste a config JSON into the
override field. The 2026-05-18 conclusion ("OBS gates Multitrack on
`multitrack_video_configuration_url`") was therefore **only correct for
the default Custom-Service flow** — not for the configured override
case.

## 3. The wire format — Enhanced RTMP Y2023

OBS implements the [Veovera Enhanced RTMP Y2023
spec](https://github.com/veovera/enhanced-rtmp). The FLV mux code is
[plugins/obs-outputs/flv-mux.c:42-66](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L42):

```c
// Y2023 spec
const uint8_t AUDIO_HEADER_EX = 9 << AUDIO_FRAMETYPE_OFFSET;
enum audio_packet_type_t {
    AUDIO_PACKETTYPE_SEQ_START = 0,
    AUDIO_PACKETTYPE_FRAMES = 1,
    AUDIO_PACKETTYPE_MULTICHANNEL_CONFIG = 4,
    AUDIO_PACKETTYPE_MULTITRACK = 5,
};

const uint8_t FRAME_HEADER_EX = 8 << VIDEO_FRAMETYPE_OFFSET;
enum packet_type_t {
    PACKETTYPE_SEQ_START = 0,
    PACKETTYPE_FRAMES = 1,
    PACKETTYPE_SEQ_END = 2,
    PACKETTYPE_FRAMESX = 3,
    PACKETTYPE_METADATA = 4,
    PACKETTYPE_MPEG2TS_SEQ_START = 5,
    PACKETTYPE_MULTITRACK = 6,
};

enum multitrack_type_t {
    MULTITRACKTYPE_ONE_TRACK = 0x00,
    MULTITRACKTYPE_MANY_TRACKS = 0x10,
    MULTITRACKTYPE_MANY_TRACKS_MANY_CODECS = 0x20,
};
```

### Multi-track video frame layout

[flv-mux.c:442-501](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442) writes:

```c
bool is_multitrack = idx > 0;
if (is_multitrack) {
    s_w8(&s, FRAME_HEADER_EX | PACKETTYPE_MULTITRACK | frame_type);
    s_w8(&s, MULTITRACKTYPE_ONE_TRACK | type);
    s_w4cc(&s, codec_id);     // FourCC: avc1 / hvc1 / av01
    s_w8(&s, (uint8_t)idx);   // track ID
} else {
    s_w8(&s, FRAME_HEADER_EX | type | frame_type);
    s_w4cc(&s, codec_id);
}
```

### Multi-track audio frame layout

[flv-mux.c:388-440](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L388) writes:

```c
bool is_multitrack = idx > 0;
if (is_multitrack) {
    s_w8(&s, AUDIO_HEADER_EX | AUDIO_PACKETTYPE_MULTITRACK);
    s_w8(&s, MULTITRACKTYPE_ONE_TRACK | type);
    s_wa4cc(&s, codec_id);    // FourCC: mp4a
    s_w8(&s, (uint8_t)idx);   // track ID
} else {
    s_w8(&s, AUDIO_HEADER_EX | type);
    s_wa4cc(&s, codec_id);
}
```

### Supported codecs

- **Video:** `avc1` (H.264), `hvc1` (HEVC), `av01` (AV1)
- **Audio:** `mp4a` (AAC)

Codec selection per-track is independent — OBS can mix codecs across
tracks (`MULTITRACKTYPE_MANY_TRACKS_MANY_CODECS`), though the typical
ladder is single-codec, multi-resolution.

## 4. Server-side reception — what nginx-rtmp and MediaMTX actually do

E-RTMP tags use FLV tag type bytes that are **not** in the legacy
0..18 enum (the high nibble carries `FRAME_HEADER_EX = 0x80` or
`AUDIO_HEADER_EX = 0x90`). nginx-rtmp doesn't recognise these as
standard frame headers, but:

- it does not validate FLV tag types strictly during RTMP message
  forwarding — the bytes are blobs to it;
- the AMF metadata it inspects to populate session info comes from
  `@setDataFrame` payloads, which OBS still sends in standard form;
- the tags pass through to any consumer of the RTMP stream (a sub-
  scriber, a record sink, or in our case the loopback ffmpeg).

**The catch**: standard ffmpeg's FLV demuxer (mainline 7.x) does not
understand `PACKETTYPE_MULTITRACK` either. It will emit warnings ("Unknown
packet type 6") and drop the unrecognised packets. So while
nginx-rtmp / MediaMTX move the bytes faithfully, the **ffmpeg-copy
fanout in Restream_Plus today would drop multi-track payload** before
re-emitting. The fix is server-side parsing before the ffmpeg stage.

## 5. What Restream_Plus would have to build

### Phase A — Pilot test (≤1 day)

A standalone Python script that listens on RTMP port 1936, decodes the
RTMP message stream into FLV tags, classifies each tag (legacy vs.
E-RTMP, multitrack vs. single, codec + track ID), and logs the
distribution. Run with OBS pointed at it, with Multitrack Video enabled
and a hand-crafted config JSON. Expected output: a series of E-RTMP
multitrack tags with track IDs 0..N and codec FourCCs matching the
override JSON.

This pilot proves:
1. OBS actually emits E-RTMP packets via Custom RTMP (the
   `custom_config_only` branch behaves as the source predicts).
2. The JSON shape we paste into OBS's override field is correctly
   accepted (the schema is undocumented — we'll be reverse-engineering
   from Twitch's responses or trial-and-error).
3. Tag layouts match `flv-mux.c` exactly (no version skew with the
   shipped OBS binary).

**Until the pilot passes, the rest of the plan is conditional.**

### Phase B — Server-side FLV demuxer (1–2 weeks)

In `app/fanout/`, add a streaming FLV demuxer that:

- consumes the raw RTMP payload from MediaMTX/nginx-rtmp via a new
  internal endpoint (not the loopback URL — that's ffmpeg-bound);
- emits per-track sub-streams keyed by `(codec_fourcc, track_id)`;
- writes each sub-stream into its own loopback ingest path (e.g.
  `rtmp://127.0.0.1:1935/live/<key>/<track_id>`);
- preserves timestamps so each track stays in sync.

Each target's worker reads from one of these track-specific paths
instead of the bundle path. The track-assignment table (which target
reads which track) becomes a per-target settings field — sensible
default: highest bitrate, manual override via UI.

### Phase C — Track-aware fanout architecture (new ADR-0016)

Beyond demux, an ABR-aware fanout could:
- Negotiate per-target bandwidth observations (drop frames, breaker
  state) → re-assign tracks dynamically.
- Expose track preferences in the dashboard (operator picks "1080p
  Twitch / 720p YouTube / 480p VK").
- Validate target compatibility (Kick limits, YouTube AV1 acceptance).

This is a major architecture shift and worth an ADR.

## 6. Downstream platform compatibility

Multi-track *ingest* is mostly Twitch-specific today; *delivery* of
multi-track from Restream_Plus to downstream platforms still has to be
single-track for everyone except Twitch:

| Platform   | Accepts multi-track RTMP? | Notes                                                                                  |
|------------|---------------------------|----------------------------------------------------------------------------------------|
| Twitch     | Yes — but only via the official Multitrack service flow, which requires the `multitrack_video_configuration_url` POST. We'd be sending to their *standard* ingest URL, so we forward single-track only. |
| YouTube    | No                        | Standard RTMP, single-track only.                                                      |
| Kick       | No                        | Standard RTMPS only.                                                                   |
| VK Live    | No                        | Standard RTMPS only.                                                                   |

**So the value of receiving multi-track from OBS is NOT to forward
multi-track downstream** — it's to let OBS do the per-rendition
encoding once and let Restream_Plus pick the right rendition per target.
Today every target reads the same loopback stream (ffmpeg-copy); a
6 Mbps OBS push is what YouTube and VK both get whether they need it or
not. With multi-track ingest, OBS could send 6 Mbps + 4 Mbps + 2 Mbps in
parallel; YouTube takes 6, VK takes 2, and OBS pays the CPU once.

## 7. Open follow-up questions

1. **JSON schema for `multitrack_video_config_override`**: undocumented
   by OBS upstream. Will need to reverse-engineer from Twitch responses
   (capturable via OBS log files) or iterate by experiment. The full
   schema lives in OBS's `GoLiveApi::Config` deserializer at
   [frontend/utility/GoLiveAPI_Network.cpp](../../reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp).
2. **Ffmpeg drop behaviour**: confirm what ffmpeg actually does when
   given an FLV stream containing E-RTMP multitrack tags. The standard
   FLV demuxer will warn and skip; can we copy the warnings out of the
   worker's stderr ring (slice-2 redaction layer captures these) to
   validate?
3. **HEVC / AV1 in fanout**: today ffmpeg-copy works because every target
   accepts H.264. If multitrack pushes HEVC/AV1 variants, only Twitch
   and (selectively) YouTube accept them. The track-assignment table
   needs codec awareness.
4. **The slice-10 `MAX_LIFETIME_SPAWN_ATTEMPTS = 1000` cap**: with
   multi-track fanout, "worker" lifecycle is per-track-per-target. The
   cap interacts in subtle ways (one OBS bounce now produces N spawns
   instead of one). Will need rethinking when Phase B lands.

## 8. References

- OBS source (shallow clone, gitignored): [reference/obs-studio/](../../reference/obs-studio/)
- [Veovera Enhanced RTMP Y2023 spec](https://github.com/veovera/enhanced-rtmp)
- Old (now-superseded) deferral conclusion:
  [docs/architecture/ADR-0003-rtmp-ingest-and-fanout.md §"Open questions"](../architecture/ADR-0003-rtmp-ingest-and-fanout.md)
  (line ~412); amendment 2026-05-19 added below the original text.
- OBS upstream issues tracked previously:
  https://ideas.obsproject.com/posts/2684/ (Custom Service support for
  multitrack_video_configuration_url),
  https://ideas.obsproject.com/posts/2743/ (sideloadable services.json).
  Neither is resolved upstream — but as this study shows, **neither is
  blocking** for the operator-supplied-JSON path.
