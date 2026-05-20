# ADR-0017 — OBS Enhanced-Broadcasting auto-config endpoint (paste-free multi-track enablement)

**Status:** Accepted (design) — implementation gated on the slimmed Phase A pilot below.
**Date:** 2026-05-20
**Relates to:** [ADR-0016](ADR-0016-enhanced-rtmp-multitrack-ingest.md) (the *receive* side — tap/demux/route). This ADR is the *send* side — how OBS is told to emit multi-track at all.

---

## Context

ADR-0016 builds everything to *receive* an Enhanced-RTMP multi-track push and forward the right track per platform. But OBS will only *emit* multi-track when something tells it to. The two ways that "something" can happen:

1. **Manual override** — the operator pastes a `multitrack_video_config_override` JSON into OBS's Custom-service settings (`MultitrackVideoOutput.cpp:413-420`, `custom_config_only` path).
2. **Auto-config over HTTP** — on Start Streaming, OBS POSTs to a service's `multitrack_video_configuration_url` and the server returns the encoder ladder. This is how Twitch ("Enhanced Broadcasting"), Amazon IVS, and Dolby/Millicast all work.

The operator's requirement is explicit: **no manual pasting, ever** — "mimic what Twitch does when Enhanced Broadcasting is turned on." That mandates path 2: Restream_Plus serves a Twitch-compatible `GetClientConfiguration` endpoint, OBS auto-fetches the ladder, the operator pastes nothing.

Deployment reality: OBS and Restream_Plus are on **different machines** — same LAN today (RP at `10.10.0.2:8000`, plain HTTP), possibly a public VPS later. RP cannot write OBS's config files, so any OBS-side artifact must be delivered, not written.

This decision is grounded in the OBS source clone (`reference/obs-studio`, commit `5dacbb6`, latest stable; Rule №7) plus the public IVS/Millicast integration docs, which publish the identical protocol.

---

## Decision

Implement a **Twitch-Enhanced-Broadcasting-compatible `GetClientConfiguration` HTTP endpoint** in Restream_Plus. On Start Streaming, OBS POSTs its capabilities + stream key; RP authenticates by the ingest key, picks a hardware-appropriate encoder ladder, and returns a `GoLiveApi::Config` that points OBS at our RTMP ingest. OBS encodes that ladder and pushes one multi-track Enhanced-RTMP connection, which the ADR-0016 tap/demuxer receives.

The manual override (path 1) is demoted to a **documented fallback**, not the primary UX.

### The wire contract (pinned from OBS source + IVS docs)

**Request — `GoLiveApi::PostData`** (`reference/obs-studio/frontend/utility/models/multitrack-video.hpp:192`, serialised in `GoLiveAPI_PostData.cpp`):

- `service` (OBS hardcodes `"IVS"`), `schema_version` (`"2025-01-25"` on current stable; older builds `"2024-06-04"` — echo whatever arrives, do not hard-validate), `authentication` (the stream key).
- `client { name:"obs-studio", version, supported_codecs[] }` — `supported_codecs` is OBS's enumerated encodable set (`h264`, `h265`, `av1`).
- `capabilities { cpu{physical_cores,logical_cores,...}, memory{total,free}, gpu[{model,vendor_id,device_id,dedicated_video_memory,driver_version}], system{...} }`.
- `preferences { maximum_aggregate_bitrate, maximum_video_tracks, vod_track_audio, audio_*, canvases[{width,height,canvas_width,canvas_height,framerate}] }`. (Older builds send a flat `width/height/framerate` instead of `canvases[]` — tolerate both.)

**Response — `GoLiveApi::Config`** (`multitrack-video.hpp:205-284`, deserialised in `GoLiveAPI_Network.cpp:95`):

```json
{
  "meta":   { "service": "restream-plus", "schema_version": "2025-01-25", "config_id": "<uuid4>" },
  "status": { "result": "success" },
  "ingest_endpoints": [
    { "protocol": "RTMP", "url_template": "rtmp://<advertised>/live/{stream_key}" }
  ],
  "encoder_configurations": [
    { "type": "jim_nvenc", "width": 1920, "height": 1080,
      "framerate": {"numerator": 60, "denominator": 1},
      "canvas_index": 0,
      "settings": {"rate_control":"CBR","bitrate":6000,"keyint_sec":2,"profile":"high","preset2":"p5","tune":"hq"} }
  ],
  "audio_configurations": { "live": [ {"codec":"aac","track_id":0,"channels":2,"settings":{"bitrate":160}} ] }
}
```

OBS-mandatory for a successful start (else hard-fail / refuse to stream, `MultitrackVideoOutput.cpp:658`):
- `status.result != "error"` (an `"error"` → critical dialog; `"warning"` + empty configs → also fails).
- non-empty `encoder_configurations[]`.
- at least one `ingest_endpoints[]` whose `protocol` matches the operator's chosen protocol; `url_template` carries the literal `{stream_key}` placeholder.
- non-empty `audio_configurations.live[]`.
- `meta.config_id` present (opaque; OBS echoes it as a `?clientConfigId=` query arg on the RTMP URL — harmless to our nginx-rtmp `on_publish`, which keys on the stream name).

### Encoder-type selection is load-bearing (not a static ladder)

OBS encodes the returned `encoder_configurations[]` **verbatim** — zero negotiation — and **hard-fails if the named `type` isn't available on the client** ("Encoder type '%s' not available", `MultitrackVideoOutput.cpp:233`). There is no fallback list. Therefore the endpoint MUST choose `type` from the POSTed `capabilities.gpu` + `client.supported_codecs`:

- **Hardware GPU encoding only — never CPU/x264 (operator directive).** The encode box has a capable GPU ("put 5069 to work"); the CPU must not do encode work. The endpoint selects a hardware encoder from `capabilities.gpu[].vendor_id`:
  - NVIDIA (`0x10DE`) → `jim_nvenc` (H.264) / `jim_hevc_nvenc` (HEVC) / NVENC AV1 (`av01`, RTX-40+ only — gate on the exact GPU model).
  - AMD (`0x1002`) → `h264_texture_amf` / HEVC AMF.
  - Intel (`0x8086`) → `obs_qsv11_v2`.
- **`obs_x264` is NOT an acceptable ladder entry.** If the POSTed capabilities show no usable hardware encoder, the endpoint **fails loud** — returns `status.result:"error"` with a clear message ("no supported hardware encoder detected") and refuses to hand OBS a config — rather than silently falling back to CPU encoding. This follows the project's no-silent-fallback rule and the operator's explicit "no CPU encoding."
- Per-codec routing (the actual product win, ADR-0016 §6): offer one H.264 NVENC track for everyone + one modern-codec NVENC track (HEVC/AV1) for YouTube **only when the GPU can encode it** (`supported_codecs` + model gate). Never advertise a codec the client can't hardware-encode.

### What we deliberately do NOT replicate

Twitch's *ingest* is strict: it embeds the ladder inside the stream-key token and disconnects if published tracks don't match (count/res/fps/bitrate), and requires IDR-aligned PTS + BPM-SEI metadata before every IDR. **That is Twitch-ingest enforcement, not part of the OBS contract.** Our nginx-rtmp / MediaMTX simply accepts the publish. So we ignore all of it: we only need a valid HTTP response so OBS *starts*; then we receive whatever OBS sends and demux it (ADR-0016). This keeps the scope to "answer the HTTP POST," not "reimplement Twitch's ingest."

### Enablement (how OBS gets pointed at us)

Ranked by robustness, pending pilot confirmation:

1. **`--config-url <url>` OBS launch flag** (primary candidate). `MultitrackVideoAutoConfigURL()` (`GoLiveAPI_Network.cpp:111-127`) checks this flag *before* the service setting. A desktop shortcut / our launcher sets `--config-url http://<rp>/api/.../GetClientConfiguration`. No services.json, no AppData clobber, no format-version fragility. **Open question (pilot):** whether the flag alone also enables the multitrack output, or whether the Settings→Stream checkbox (gated on a service carrying a config URL) is still required.
2. **Generated `services.json`** (documented fallback). RP serves a `format_version: 5` services.json baked with our config-URL + RTMP-ingest-URL; operator drops it into `%APPDATA%/obs-studio/plugin_config/rtmp-services/` once and selects "Restream_Plus" in the dropdown. Risks (Rule №6 footguns): OBS's own service-updater writes the same dir and can shadow it; a `format_version` bump (5→6) makes it silently vanish.
3. **OBS Lua/Python script** — can select a *bundled* multitrack service and toggle it, but **cannot** cleanly inject a custom `multitrack_video_configuration_url`: for `rtmp_common` services OBS re-derives that URL from the bundled services.json by service name (`rtmp-common.c:580`), overwriting a script value. So a script is not a reliable custom-URL route. Rejected as primary.

### Security model

The stream key rides in the POST body (`PostData.authentication`), so transport confidentiality matters:

- **Auth:** the endpoint validates `authentication` against the configured ingest key using the existing timing-safe `keys_match` logic (current + grace-windowed previous), the same as the nginx-rtmp `on_publish` webhook. No cookie/session (OBS isn't logged in). Locked-key state → 503.
- **LAN (today):** plain HTTP is acceptable on a trusted private network (matches the current `RESTREAM_COOKIE_SECURE=false` posture).
- **VPS (future):** plain HTTP would leak the key. **Self-signed TLS is not an option** — OBS's libcurl has `SSL_VERIFYPEER` on with no trust-this-cert UI (`RemoteTextThread.cpp`), so a self-signed cert silently fails the POST. The supported secure paths are **network-layer trust (Tailscale/WireGuard)** — give both boxes tailnet IPs, config URL is `http://<tailnet-ip>:8000/...`, encrypted with zero cert management — or a **real cert via a reverse proxy (Caddy/Let's-Encrypt)**.
- **Fail-loud TLS gate:** an explicit `RESTREAM_LAN_ONLY_RELAXED_TLS` flag (default `false`). When false, RP refuses to advertise / serve a non-TLS config URL to a non-RFC-1918 peer rather than leaking the key silently.

---

## Slimmed Phase A pilot (replaces ADR-0016's "capture the JSON")

A real OBS (latest stable, operator's box) must confirm what the source can't:

- [ ] OBS, pointed at our endpoint (via `--config-url` and/or a generated services.json), actually POSTs `GetClientConfiguration` to us and starts streaming on a `status:"success"` response with an NVENC (`jim_nvenc`) ladder matched to the operator's GPU.
- [ ] Whether `--config-url` alone enables the multitrack output, or the service+checkbox is also required.
- [ ] Field tolerances for the operator's exact OBS version (`schema_version`, `canvases[]` vs flat, which `capabilities` fields arrive).
- [ ] The push that results is genuinely multi-track (`PACKETTYPE_MULTITRACK = 6`), so the ADR-0016 demuxer sees >1 track.
- [ ] Capture that multi-track FLV → commit as `tests/fixtures/multitrack_real.flv` for the ADR-0016 demuxer/parser tests (replacing the synthetic vectors with a real one).

If `--config-url` is insufficient and services.json is required, the clobber/format-version risks above become live and we either accept the fragility or pursue an upstream OBS-services contribution (out of scope).

---

## Consequences

- **Positive:** paste-free, Twitch-equivalent operator experience; the rendition/codec ladder is configured in RP's own UI (where targets already live), not in OBS; reuses the existing ingest-key auth; the endpoint is supervisor-independent (reads config, returns JSON).
- **Negative / risk:** we are (per research) the first known *self-hosted* implementer of this protocol — Twitch/IVS/Millicast are all commercial. Brittleness vectors: OBS schema/format-version drift (mitigated by lenient parsing + version-detect → readable `status:"error"`), encoder-type/hardware mismatch (mitigated by capability-aware NVENC/AMF/QSV selection; fail-loud if no HW encoder, never an x264 fallback), and VPS transport security (mitigated by Tailscale/Caddy, never self-signed).
- **Proportionality note (honest):** for pure re-push, per-*resolution* renditions are largely redundant (platforms re-transcode). The durable win is per-platform *codec* selection (AV1/HEVC→YouTube, H.264→Twitch), which platform transcoding cannot give. The endpoint should bias toward a small codec-focused ladder, not a deep resolution ladder, unless the operator explicitly wants more.

---

## Implementation slices (post-pilot, per slice discipline)

1. **`keys_match` extraction** — lift the duplicated ingest-key comparison from `internal_rtmp.py` + `internal_mtx.py` into `app/auth/ingest_key.py` (prerequisite; closes existing duplication).
2. **Endpoint core** — `POST /api/.../GetClientConfiguration`: stream-key auth, capability-aware encoder-type selection, `GoLiveApi::Config` response, fail-loud when no ladder / no advertised URL. Plus the operator-configurable ladder (settings).
3. **Enablement** — `--config-url` launcher support + a generated `services.json` download; the `RESTREAM_LAN_ONLY_RELAXED_TLS` gate.
4. **UI** — ladder config panel + "connect OBS" helper in Settings.

Then ADR-0016's C1/C2 receive-side wiring consumes the resulting multi-track push.
