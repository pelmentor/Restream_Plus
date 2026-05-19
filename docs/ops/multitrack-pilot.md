# Multi-track Phase A pilot — operator runbook

**Purpose:** empirically validate the assumptions underlying [ADR-0016](../architecture/ADR-0016-enhanced-rtmp-multitrack-ingest.md) before any Phase B+ code is written. The pilot is **operator-driven** — Claude cannot trigger OBS publishes from outside; the operator has to run OBS by hand. Expected effort: ≤ 1 day. **No application code changes during this phase.**

**Status:** not yet started. ADR-0016 implementation is gated on this passing.

## Why a pilot first

Three assumptions in ADR-0016 are unverified end-to-end:

1. OBS with `custom_config_only` + a user-supplied `multitrack_video_config_override` JSON actually emits `PACKETTYPE_MULTITRACK = 6` tags ([reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442)). Source-level reading suggests yes; production reality unconfirmed.
2. ffmpeg `-c copy -f flv` reading from the loopback RTMP URL forwards the multi-track FLV tags intact, or silently drops them as "unknown packet type". This determines whether ingest tap Option A (ffmpeg) is viable or whether we have to fall back to Option B (Python RTMP client) — a 2-week scope difference.
3. The `multitrack_video_config_override` JSON schema is undocumented by OBS upstream. The operator must produce a working JSON empirically (capture from a real Twitch session, or iterate from a minimal example).

Until these three are verified by capture-and-analyse, Phase B–E is "code against assumptions" — exactly what Rule №1 forbids.

## Procedure overview

```
   OBS (Custom Service + Multitrack + override JSON)
        │
        │  RTMP push (Enhanced RTMP if assumption (1) holds)
        ▼
   MediaMTX dev side-car (port 1935 loopback)
        │
        │  ffmpeg -i rtmp://127.0.0.1:1935/live/<key> -c copy -f flv capture.flv
        ▼
   capture.flv on disk
        │
        │  scripts/pilot_ertmp.py capture.flv
        ▼
   tag-level analysis: legacy vs E-RTMP, multi-track yes/no,
   codec FourCC + track ID, sequence-header detection
```

## Prerequisites

1. **Local dev environment up:** [`start.bat`](../../start.bat) launches MediaMTX dev side-car + backend + frontend on the Windows dev box. Confirm panel reachable at `http://localhost:8000` and `rtmp://localhost:1935/live` accepts test publishes.
2. **OBS Studio installed.** Any recent build (≥29.1) supports Enhanced RTMP. The pilot was scoped against the source at [reference/obs-studio/](../../reference/obs-studio/) (shallow clone, 2026-05-19); operator's OBS binary need not match exactly.
3. **At least one enabled target with credentials** in Restream_Plus (so auto-run-on-publish doesn't fail at start with "no targets enabled"). Twitch with a test channel is fine.
4. **ffmpeg available on PATH** (the dev launcher already requires this).
5. **The `multitrack_video_config_override` JSON.** This is the empirical unknown — see "JSON schema discovery" below.

## Step 1 — Configure OBS for Custom Service + Multitrack Video

Per [reference/obs-studio/frontend/settings/OBSBasicSettings.cpp:5597-5633](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5597) the Multitrack Video group is rendered:

- Always when `IsCustomService()` is true (the conditional that gates on `multitrack_video_configuration_url` is short-circuited for Custom Service per [OBSBasicSettings.cpp:5599](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5599)).
- The `multitrack_video_config_override` field is rendered only when both `Custom Service` is selected AND the `Enable Multitrack Video` checkbox is checked ([OBSBasicSettings.cpp:5631](../../reference/obs-studio/frontend/settings/OBSBasicSettings.cpp#L5631)).

Steps in OBS UI:

1. **Settings → Stream → Service:** select `Custom...`.
2. **Server:** `rtmp://localhost:1935/live` (or whatever the dev launcher binds — usually 1935).
3. **Stream Key:** the test ingest key from Restream_Plus's Settings → Ingest key (reveal once, paste).
4. **Enable Multitrack Video:** check the box. The `Multitrack Video Config Override` text field appears below.
5. **Paste the JSON** (see next section). Settings → OK.

## Step 2 — Produce the override JSON empirically

The OBS source-level entry point is `MultitrackVideoOutput::custom_config` deserialised in [reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp:413](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L413). The deserialiser shape lives in `GoLiveApi::Config` ([reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp](../../reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp)) — read this when you want to know exactly which JSON keys OBS will accept.

Two approaches to obtain a working JSON:

### Approach A — capture from a real Twitch session

1. In OBS, switch service to `Twitch` (officially supported Multitrack), point at a test Twitch channel, enable Multitrack Video, click Start Streaming.
2. OBS will POST to `https://ingest.twitch.tv/api/v3/GetClientConfiguration` ([reference/obs-studio/plugins/rtmp-services/data/services.json:9](../../reference/obs-studio/plugins/rtmp-services/data/services.json#L9)) and receive a JSON response. This response is the canonical shape — Twitch is the reference consumer.
3. In OBS logs (`Help → Log Files → View Current Log`), search for the `GoLiveApi::Config` payload. Copy it.
4. Stop streaming. Switch service back to `Custom...`. Paste the captured JSON into `Multitrack Video Config Override`.

Caveat: Twitch's response includes a `meta.config_id` and ingest-URL fields. The `meta.config_id` is fine to keep; the ingest URL is overridden by the Server field. Other fields (encoder configurations, audio configurations) should map 1:1 to what we want to test against Restream_Plus.

### Approach B — iterate from a minimal example

Start with the smallest plausible JSON and let OBS's error messages drive the shape:

```json
{
  "meta": { "config_id": "restream_plus_pilot_1" },
  "encoder_configurations": [
    {
      "type": "obs_x264",
      "width": 1920,
      "height": 1080,
      "framerate": { "numerator": 30, "denominator": 1 },
      "bitrate": 6000,
      "settings": { "rate_control": "CBR", "profile": "high" }
    },
    {
      "type": "obs_x264",
      "width": 1280,
      "height": 720,
      "framerate": { "numerator": 30, "denominator": 1 },
      "bitrate": 4000,
      "settings": { "rate_control": "CBR", "profile": "high" }
    }
  ],
  "audio_configurations": {
    "live": [
      {
        "type": "ffmpeg_aac",
        "channels": 2,
        "bitrate": 128,
        "settings": {}
      }
    ]
  }
}
```

Try Start Streaming. If OBS rejects with a parser error, iterate by reading the OBS log + the GoLiveAPI deserialiser source. The result of iteration is the JSON we commit to the pilot fixture.

**Time-box this approach at 2–4 hours.** If approach B is fruitless, fall back to approach A (a working Twitch test account is the shortcut).

## Step 3 — Capture the FLV byte stream

While OBS is publishing to Restream_Plus, run in a separate terminal:

```powershell
ffmpeg `
  -hide_banner -loglevel warning -nostats `
  -i rtmp://localhost:1935/live/<ingest-key> `
  -c copy `
  -f flv `
  capture.flv
```

Let it run for ~30 seconds (enough for several keyframes per rendition). Ctrl-C to stop.

`<ingest-key>` is the same key OBS is configured with — Restream_Plus's auth webhook will accept the loopback ffmpeg as another consumer; the supervisor's worker will also be reading the same stream, so two consumers are pulling concurrently. This is fine — MediaMTX / nginx-rtmp permit multiple subscribers.

**If ffmpeg complains** about unknown packet types during the capture, that's actually the answer to assumption (2): ffmpeg is dropping multi-track tags. **Save the ffmpeg stderr output** — it's diagnostic.

## Step 4 — Analyse the capture

Run the pilot analysis script (to be written; placeholder spec in ADR-0016 §12). The script:

1. Opens `capture.flv` as a byte stream.
2. Skips the FLV header (9 bytes) + initial back-reference (4 bytes).
3. In a loop:
   - Reads `tag_type` (1 byte), `data_size` (3 bytes), `timestamp_lower` (3 bytes), `timestamp_ext` (1 byte), `stream_id` (3 bytes).
   - Reads `data_size` bytes for the tag body.
   - Reads 4 bytes for the back-reference.
4. For each tag:
   - If `tag_type == 9` (video): inspect byte 0 of the body. `(byte0 & 0x80)` set → Enhanced RTMP. Extract `packet_type = byte0 & 0x0F`. If `packet_type == 6` → multi-track; parse `multitrack_type`, `codec_fourcc`, `track_id` per [reference/obs-studio/plugins/obs-outputs/flv-mux.c:442](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442).
   - If `tag_type == 8` (audio): same idea with `AUDIO_HEADER_EX = 0x90`.
5. Print summary: `total_tags`, `legacy_tags`, `ertmp_tags`, `multitrack_tags`, `tracks_seen = {(track_id, codec_fourcc) : count}`.

## Exit gates

The pilot is "passing" when ALL of these are true:

- [ ] OBS log shows a successful publish (no `FailedToStartStream.NoConfig` per [reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp:449](../../reference/obs-studio/frontend/utility/MultitrackVideoOutput.cpp#L449)).
- [ ] `capture.flv` exists and is non-empty.
- [ ] Analysis script reports `multitrack_tags > 0`.
- [ ] `tracks_seen` contains entries for at least 2 distinct `track_id` values.
- [ ] Tag layout matches the byte-level spec at [reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501](../../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442) (codec FourCC at offset 2, track ID at offset 6 for `MULTITRACKTYPE_ONE_TRACK = 0x00`).
- [ ] `capture.flv` is small enough (~5 MB or less) to commit to `tests/fixtures/multitrack_pilot.flv`. If larger, pin sha256 + size in CI fixture spec.

## Failure modes

### "OBS won't start streaming"

- Likely cause: malformed override JSON. Read OBS log; the `GoLiveApi::Config` deserialiser is strict.
- Fix: iterate JSON shape against the source ([reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp](../../reference/obs-studio/frontend/utility/GoLiveAPI_Network.cpp)).

### "ffmpeg capture is empty"

- Likely cause: ingest key mismatch, OBS not actually publishing.
- Fix: verify Restream_Plus's panel shows the publish-began webhook fired (Recent Events menu).

### "Analysis script reports multitrack_tags == 0"

- This is **the assumption (1) failure**. Either OBS isn't emitting multi-track despite the override (deserialiser silently fell back to single-track), or the captured FLV is post-ffmpeg-drop (assumption (2) failure).
- Diagnostic: re-run with `-loglevel verbose` on the ffmpeg capture; look for "Unknown packet type 6" warnings.
- If ffmpeg IS dropping the tags during `-c copy`: Option A (ffmpeg tap) is dead; Phase B has to go straight to Option B (Python RTMP client). Add ~2 weeks scope to ADR-0016 Phase B.

### "JSON schema is too painful to iterate"

- Approach A (capture from a real Twitch session) is the escape hatch. A free Twitch dev account is enough.

## Hand-off after pilot passes

1. Commit `capture.flv` (or sha256-pinned spec) to `tests/fixtures/multitrack_pilot.flv`.
2. Commit the working `multitrack_video_config_override` JSON to `tests/fixtures/multitrack_override_example.json` with a comment noting "empirically validated against OBS &lt;version&gt; on &lt;date&gt;".
3. Update [ADR-0016 §12 Phase A](../architecture/ADR-0016-enhanced-rtmp-multitrack-ingest.md) status to "Passed YYYY-MM-DD".
4. Phase B is now unblocked.

## Hand-off if pilot fails on assumption (2) (ffmpeg drops the tags)

1. Update ADR-0016 §1 — "Decision: Option A" becomes "Decision: Option B (after pilot showed ffmpeg drops multi-track tags during `-c copy`)".
2. ADR-0016 §12 Phase B scope grows: implement a Python RTMP subscriber instead of `FlvTap`. Estimated +2 weeks.
3. Phase A still considered "passed" — we got our answer, even if it was the unfavorable one.
