# Platform reference — RTMP/RTMPS ingest endpoints

Verified by a research subagent against OBS Studio's canonical
`services.json` and each platform's official documentation as of
**May 2026**. Quarterly re-verification is part of the maintenance
checklist (`docs/ops/maintenance.md`, TBD).

These constants ship as **defaults** in the application. Users can
override any URL in the per-target Settings — overrides are useful when
a platform rotates regional ingest hosts or when a custom RTMP
destination is added (e.g., a private TwitchUploads / Restreaming
appliance).

## Twitch

| Field         | Value                                                                          |
| ------------- | ------------------------------------------------------------------------------ |
| Protocol      | RTMP (default) / RTMPS supported                                               |
| Default URL   | `rtmp://live.twitch.tv/app`                                                    |
| Regional URLs | 47 variants in OBS services.json (e.g., `live-jfk.twitch.tv`, `live-lax`, etc.) |
| Key delivery  | Path segment after `/app/`. Full: `rtmp://live.twitch.tv/app/<key>`           |
| Constraints   | H.264 only, ≤ 6000 kbps video, ≤ 320 kbps audio, 2 s keyframe interval        |
| Source        | OBS Studio rtmp-services repo, `services.json`                                 |

**UI affordance:** "Auto" is the default; users can pick a regional
ingest from a dropdown populated from a pinned OBS services.json
snapshot (the snapshot lives at `app/services/twitch_ingests.json`,
refreshable via CLI but stable per image build).

## YouTube Live

| Field             | Value                                                                                |
| ----------------- | ------------------------------------------------------------------------------------ |
| Protocol          | RTMPS (default, port 443) / RTMP supported                                           |
| Primary URL       | `rtmps://a.rtmps.youtube.com:443/live2`                                              |
| Backup URL        | `rtmps://b.rtmps.youtube.com:443/live2?backup=1`                                     |
| Key delivery      | Path segment after `/live2/`. Full: `rtmps://a.rtmps.youtube.com:443/live2/<key>`    |
| Backup with key   | `rtmps://b.rtmps.youtube.com:443/live2/<key>?backup=1` — query stays AFTER the key   |
| Constraints       | H.264 / HEVC / AV1, ≤ 51000 kbps, 2 s keyframe interval, audio ≤ 160 kbps           |
| Backup semantics  | YouTube uses backup as hot-failover; push **the same key** to both concurrently     |
| Source            | OBS Studio rtmp-services repo, `services.json`                                       |

**UI affordance:** A single "Use backup ingest" toggle in YouTube target
settings. When on, we spawn two ffmpeg workers for the YouTube target —
this is the only target that legitimately wants 2× outputs for the same
key. Tracked as one target in the data model, two workers at runtime.

## Kick

| Field        | Value                                                                                 |
| ------------ | ------------------------------------------------------------------------------------- |
| Protocol     | RTMPS (port 443)                                                                       |
| URL          | `rtmps://fa723fc1b171.global-contribute.live-video.net:443/app`                       |
| Key delivery | Path segment. Full: `rtmps://fa723fc1b171.global-contribute.live-video.net:443/app/<key>` |
| Backend      | AWS IVS (hostname is an IVS contribute endpoint, stable but not vanity-branded)        |
| Constraints  | H.264 only, ≤ ~8000 kbps, 2 s keyframe, CBR. Not in OBS services.json — custom service |
| Source       | Kick help center (article IDs 7066931, 7135578)                                        |

**UI affordance:** Plain "URL + key" form. The non-vanity hostname will
look suspicious to users; the form ships with an info tooltip:
*"Kick's ingest is hosted on AWS IVS — this is the official endpoint."*

## VK Video Live — **special case**

| Field             | Value                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------- |
| Protocol          | RTMP                                                                                                    |
| Web-UI URL        | `rtmp://ovsu.okcdn.ru/input/` (current as of Jan 2025; what vkvideo.ru shows in the UI)                 |
| API URL           | `rtmp://vp.vkforms.ru/live/` (returned by `video.startStreaming` API)                                   |
| `live.vkvideo.ru` | **UNVERIFIED** as RTMP ingest — appears to be web frontend only                                          |
| Key delivery      | Path segment. Full: `rtmp://ovsu.okcdn.ru/input/<key>`                                                  |
| Key lifetime      | **SINGLE-USE per broadcast.** Key must be refreshed in VK creator UI (or via API) before each session.  |
| Constraints       | H.264 / H.265, recommended 720p @ 2.5 Mbps / 128 kbps audio, 2 s keyframe                              |

### VK design implications (load-bearing)

1. The data model for a VK target stores the **URL** as a persistent
   field (default `rtmp://ovsu.okcdn.ru/input/`), and the **stream key**
   as a per-session field that defaults to empty.
2. When the user clicks START with a VK target enabled and its key is
   empty, the Dashboard shows a blocking inline prompt: *"VK requires a
   fresh stream key for each broadcast — paste it now"*.
3. After a session ends, the VK target's key is automatically wiped
   from the DB (encrypted-or-not, gone). The audit log records "VK
   session ended; key cleared" but never the key value.
4. The Settings page for a VK target explicitly does not have a
   "Stream key" field — only URL. The key is collected at START time on
   the Dashboard. This is the deliberate departure from the other three
   platforms, justified by VK's actual behavior.
5. A future enhancement (not v1): OAuth integration with VK Connect to
   call `video.startStreaming` directly, eliminating the paste step.
   Out of scope for v1. The architecture leaves room — the target type
   discriminator (`type: "vk_live"`) selects the start-flow behavior.

This is the kind of "real world is jagged" detail that a generic
"add stream key in Settings" UX would hide and silently fail at. The
right answer is to make the UX explicit, even at the cost of asymmetry
across the four platforms.

## Custom target (escape hatch)

| Field        | Value                                  |
| ------------ | -------------------------------------- |
| Protocol     | Anything ffmpeg can `-f flv` to        |
| URL          | User-supplied                          |
| Key delivery | User-supplied (or empty if URL has it) |
| Constraints  | None enforced — user's problem         |

The Custom target type lets the user push to any RTMP/RTMPS endpoint —
their own server, a niche platform, etc. Validation in the UI is loose
(checks scheme is `rtmp(s)://`, host parses) but ffmpeg's behavior is
the final arbiter.

## Source quality note

The research subagent flagged that Kick's help center returns 403 to
non-browser clients. The URL above is from search-engine-cached text
of an official help article; before each release we **manually
spot-check the Kick URL in a real browser** as part of the release
checklist. This is documented in `docs/ops/release-checklist.md` (TBD).
