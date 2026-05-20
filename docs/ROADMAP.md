# Restream_Plus Roadmap

**Last updated:** 2026-05-20
**Purpose:** single-page grand plan. Cold-resume entry point. Everything
else (`SESSION_HANDOFF.md`, ADRs, audit burndowns) is reachable from
here. If you're picking up this project after a compact / context loss /
fresh conversation, read this first.

---

## Current state

- **Production:** `ghcr.io/pelmentor/restream-plus:v1.1.3` running on operator's Unraid box (10.10.0.2:8000, plain HTTP, bind-mount `/mnt/user/appdata/restream-plus`). Five releases shipped 2026-05-18 (v1.0.0 → v1.1.3); **every release before v1.1.3 has a known browser-blocking bug — do NOT pull `:v1.0.0` through `:v1.1.2`**.
- **LOCAL-DEV MODE:** no GHCR image builds or tag pushes without explicit operator authorisation. `build-image.yml` only fires on push to `main`, so merging into `main` triggers `:edge` + `:sha-<short>` automatically.
- **Hex audit:** terminal state. Both burndown files at 0 open findings (`docs/audit/hex-audit-2026-05-18.md` 87/0, `docs/audit/hex-audit-2026-05-19.md` 31/0). Future regressions need a fresh hex-audit run.
- **Open PR stack — 10 PRs (#14 → #23), all stacked, awaiting operator dev-machine test/merge.** Each base auto-rebases when its parent merges; merge oldest-first. `backend-lint` + `backend-lockfile` CI (red on #14/#15 from a pre-existing ruff backlog + a stale `hypothesis` pin) was drained green on #14 (`chore(ruff)` 46 lint/14 format fixes + `chore(deps)` hypothesis 6.152.7→6.152.9) and cascaded through the stack — all 5 required checks now green on all PRs.

  | PR | branch | what |
  |---|---|---|
  | #14 | `audit/hex-burndown-slices-8-to-10` | hex audit slices 8/8.5/9/10 + ruff backlog drain + hypothesis lockfile bump. Base `main`. |
  | #15 | `feat/auto-run-on-publish` | auto-run-on-publish (ADR-0015) |
  | #16 | `docs/roadmap-and-adr-0016` | this ROADMAP + ADR-0016 + multitrack-pilot runbook |
  | #17 | `feat/pilot-ertmp-analyser` | pilot FLV CLI analyser (`scripts/pilot_ertmp.py`) |
  | #18 | `feat/flv-parser-module` | **B1** prod FLV parser `app/fanout/flv/parser.py` |
  | #19 | `feat/codec-compat-and-bus-events` | **B2** `accepted_video_codecs` + `TrackManifestEvent` + `WorkerSpec.stdin_provider` |
  | #20 | `feat/flv-tap` | **B3** `FlvTap` (ffmpeg `-c copy -f flv` ingest fork) |
  | #21 | `feat/sequence-header-parser` | **B4** SPS/VPS/OBU → resolution (verified vs 9 real ffmpeg vectors) |
  | #22 | `feat/flv-demuxer` | **B5** `FlvDemuxer` push-parser |
  | #23 | `docs/adr-0017-obs-autoconfig` | **ADR-0017** OBS auto-config endpoint design (+ this ROADMAP refresh) |

  All Phase-B code is receive-side and pure (no production wiring yet); `pytest -n auto` 905/905 at the stack tip. **None of this is on `main` or in any image** — LOCAL-DEV MODE, operator must dev-test + merge.

---

## Outstanding operator actions (USER-driven, none blocking each other)

These are todo items that only the operator can perform; Claude cannot:

1. **GHCR cleanup** — delete orphan tag versions from v1.0.0's stillborn-#3 green run (`:1.0.0`, `:1.0`, `:1`, `:sha-ffb1398`). Add deprecation labels on `:v1.0.0` / `:v1.1.0` / `:v1.1.1` / `:v1.1.2` (all four have one or more browser-blocking bugs). Reachable via [GitHub package settings](https://github.com/users/pelmentor/packages/container/restream-plus). Alternative: `gh auth refresh -s read:packages,delete:packages` + per-version `gh api -X DELETE`.
2. **GHCR retention policy** — set via UI per `docs/ops/ghcr-retention.md`: `Retain for: 30 days`, `Apply to: Untagged versions`.
3. **Kick browser spot-check** — Kick blocks non-browser clients per WebFetch. Confirm ingest URL still `rtmps://fa723fc1b171.global-contribute.live-video.net:443/app` via Kick help articles 7066931 / 7135578 in a real browser.
4. **Real-RTMP smoke against `:v1.1.3`** — release-checklist step 12b. OBS → `:v1.1.3` container → at least one platform end-to-end. Verify live-stats UI works.
5. **Announce v1.1.3** — whatever channel.
6. **Rotate leaked secrets** (still live, captured in local memory): admin password + ingest key. The plaintext is in `memory/project-restream-plus.md`; it was redacted from `docs/SESSION_HANDOFF.md` before commit (PR #14).
7. **Dev-machine test of PR #14 + PR #15** — required before merging either. Merging #14 auto-publishes `:edge` + `:sha-<short>` to GHCR.

---

## v1.1.4 release prep (gated on operator dev-machine test)

When operator authorises:
1. Test PR #14 + PR #15 stack on dev machine (checking out `feat/auto-run-on-publish` gives both stacks at once).
2. Merge #14 first → `build-image.yml` publishes `:edge` and `:sha-<short>`.
3. Merge #15 → `build-image.yml` publishes again with auto-run-on-publish included.
4. Tag `v1.1.4` per release process → `release.yml` builds + publishes the tagged release.
5. Operator pulls `:v1.1.4` on Unraid + smoke-tests + announces.

**Cumulative diff scope of v1.1.4** = slice 8 transactional audit-log + slice 8.5 retention hardening + slice 9 ADR docs + slice 10 medium cleanup (FG-F16, BA-F9/F13/F15/F19, FG2-H2/H4/M2/M5/L2, BA2-F5/F8, FG3-F5/F9/F10, UX-F10/F11/F12/F14, UI-F8/F14, CR-F8 through CR-F12, SA-F14 youtube_backup_enabled gate, CR2-Re-F4 type alias) + auto-run-on-publish (ADR-0015: no manual Start/Stop, OBS publish is the only trigger, immediate stop on publish-end).

---

## Multi-track / Enhanced RTMP support (ADR-0016 receive + ADR-0017 send)

The feature has **two halves**, both designed, with the receive half largely built:

- **RECEIVE side — [ADR-0016](architecture/ADR-0016-enhanced-rtmp-multitrack-ingest.md):** always-on `FlvDemuxer` (single-track fast path), ffmpeg-tap (Option A), stdin-pipe distribution to per-target workers, per-target `track_preference`, fail-loud codec compat. Source-level evidence in [docs/research/obs-multitrack-feasibility.md](research/obs-multitrack-feasibility.md).
- **SEND side — [ADR-0017](architecture/ADR-0017-obs-autoconfig-endpoint.md):** a Twitch-Enhanced-Broadcasting-compatible `GetClientConfiguration` HTTP endpoint so OBS auto-fetches the encoder ladder and emits multi-track with **zero manual JSON pasting** (the operator's hard requirement). RP authenticates by the ingest key, returns a `GoLiveApi::Config`, points OBS at our RTMP ingest.

**Why this matters:** OBS encodes the ladder once on the GPU; Restream_Plus routes the right track per platform. The durable win is **per-platform codec** (AV1/HEVC→YouTube, H.264→Twitch) — platform transcoding can't give you a better *source* codec — plus per-platform bitrate fit.

### Locked product decisions (operator, 2026-05-20) — see `memory/project-multitrack-hardware-encoding.md`

- **NVENC only.** Ladder advertises NVIDIA NVENC encoder ids exclusively (`jim_nvenc` / `jim_hevc_nvenc` / NVENC AV1). Never x264 / AMF / QSV. Fail-loud (`status:"error"`) if the client GPU isn't NVIDIA. Stream PC = **RTX 5060** (Blackwell — NVENC does H.264 + HEVC + AV1, so AV1→YouTube is in reach).
- **Ladder OBS encodes:** tier0 **1080p60 @ 12000 kbps** (operator-fixed), tier1 **720p60** (~8000), tier2 **480p ~30** (~2500). 720/480 bitrates + exact NVENC encoder-id strings finalised at build (EB2) from OBS source.
- **Routing policy:** deliver the **top tier (1080p60@12000) to every target by default; step down only when a platform forces it.** Twitch is the known forced-downgrade case — non-affiliate (no transcode → source-only) + ~8500 ingest ceiling, so **Twitch never gets 12000** (that's an ingest ceiling, not a transcode limit). Default Twitch to the highest legal tier; the per-target dropdown overrides.
- **Per-target tier is operator-configurable, never hardcoded** (`track_preference`, ADR-0016 §6). Handles affiliate-vs-not transparently: an affiliate/partner (has transcode → viewer rungs) bumps Twitch up to ~8500; a non-affiliate keeps it light (720p60@5000 / 1080p60@6000). Same OBS ladder, different dropdown.

### Phase B — RECEIVE-side modules — ✅ DONE (PRs #18–#22, all green, 905 tests)

| Slice | PR | Module |
|---|---|---|
| B1 | #18 | `app/fanout/flv/parser.py` — pull FLV/E-RTMP tag parser |
| B2 | #19 | `TargetTypeSpec.accepted_video_codecs` + `TrackManifestEvent`/`TrackInfo` + `WorkerSpec.stdin_provider` |
| B3 | #20 | `app/fanout/flv/tap.py` — `FlvTap` ffmpeg ingest fork |
| B4 | #21 | `app/fanout/flv/sequence_header.py` — SPS/VPS/OBU → resolution (verified vs 9 real ffmpeg vectors) |
| B5 | #22 | `app/fanout/flv/demuxer.py` — `FlvDemuxer` push-parser |

### Remaining slices (the build queue)

**SEND side (ADR-0017 — do these next; they're what makes OBS emit multi-track at all):**

| Slice | Scope |
|---|---|
| **EB1** | Extract `keys_match` from `internal_rtmp.py` + `internal_mtx.py` → `app/auth/ingest_key.py` (prerequisite; closes existing duplication) |
| **EB2** | `GetClientConfiguration` endpoint core — stream-key auth, NVENC-only capability-aware ladder, `GoLiveApi::Config` response; operator-configurable ladder stored in settings |
| **EB3** | Enablement — `--config-url` launch-flag support (primary; `GoLiveAPI_Network.cpp:111-127`) + generated `services.json` download + `RESTREAM_LAN_ONLY_RELAXED_TLS` fail-loud gate |
| **EB4** | Ladder-config + "connect OBS" UI in Settings |

**RECEIVE side wiring (ADR-0016, after the tap has a real source):**

| Slice | Scope |
|---|---|
| **C1** | Supervisor wiring — `_start_run_locked` tap startup, `_stop_run_locked` teardown, `FlvDemuxer` consume task, `TrackManifestEvent` emission. **Purely additive (observability) — single-track path unchanged.** |
| **C2** | `WorkerSpec.stdin_provider` → ffmpeg-worker stdin-writer; per-target `track_preference` read path; fail-loud codec compat |
| **D1** | `TrackSelector.tsx` + `useTrackManifest.ts` + per-target preference UI in `TargetDetails.tsx` |
| **E1** | Demuxer Hypothesis fuzz; long-session memory profile; codec-compat coverage; track-disappeared-mid-session test |

### Slimmed Phase A pilot (the ONE thing source can't settle — operator + real OBS)

The old "paste a `multitrack_video_config_override` JSON" pilot is **superseded by ADR-0017** (auto-config endpoint = no paste). The real-OBS smoke test now confirms, against the operator's latest stable OBS + RTX 5060:

- [ ] OBS, pointed at our endpoint (`--config-url` and/or generated `services.json`), POSTs `GetClientConfiguration` and **starts** on our `status:"success"` NVENC ladder.
- [ ] Whether `--config-url` alone enables multitrack, or the Settings→Stream checkbox (bundled-service-gated) is also required.
- [ ] The resulting push is genuinely multi-track (`PACKETTYPE_MULTITRACK = 6`) so `FlvDemuxer` sees >1 track; capture it → commit `tests/fixtures/multitrack_real.flv` (replaces the synthetic vectors).
- [ ] ffmpeg `-c copy -f flv` tap forwards the multi-track tags intact (if not → Option B Python RTMP client, +~2 weeks).

This pilot can run any time the operator has OBS up; the EB endpoint must exist first (EB2) for the auto-config path to be testable.

---

## Deferred / future / non-blocking

- **Twitch *Multitrack downstream* forwarding** — out of scope per ADR-0016 §5 (Twitch's own proprietary multi-track *ingest* API, RTX-only HEVC beta). We send Twitch ONE track on its standard RTMP ingest. (Note: ADR-0017 is the *upstream* OBS→us auto-config endpoint — a separate concern from downstream us→Twitch.)
- **HEVC/AV1 → H.264 transcode on our side** — explicitly rejected by ADR-0016 §6 (Rule №2 smell; would drag x264 into the supervisor). Codec mismatches surface as fail-loud target misconfiguration; operator adjusts OBS ladder.
- **Audio multi-track (`AUDIO_PACKETTYPE_MULTITRACK = 5`)** — zero downstream consumers as of 2026-05-19; Veovera v2 stable spec is too new. If a platform ever advertises ingest support, this is a new ADR.
- **Phase 13 deferral document** in ADR-0003 §"Open questions" is superseded by the 2026-05-19 amendment + ADR-0016 — historical bullets stay in place but **MUST NOT be cited as the current position**.
- **Fresh hex-audit run** — only if a future feature lands ≥40 new sites or a security-critical refactor. Create `docs/audit/hex-audit-YYYY-MM-DD.md` referencing 2026-05-18 + 2026-05-19 as priors.

---

## Project rules in effect

These govern collaboration with Claude on this project. Source of truth in `memory/MEMORY.md`:

- **Rule №1** — no quick fixes; proper crutchless solutions only, even if weeks/months.
- **Rule №2** — no migration baggage; greenfield, no back-compat shims.
- **Rule №3** — no questions, spawn agents (including for design choices).
- **Rule №4** — post-coding review agents before declaring done.
- **Rule №5** — post-shipping audit: verify claimed reviewer fixes / invariants / tests actually landed.
- **Rule №6** — every project audit MUST include a footgun-hunter pass.
- **Rule №7** — for any OBS protocol / wire-format / behavior work, cite `reference/obs-studio/` source files maximally (added 2026-05-19 after the hex-audit-era under-citation that produced the wrong "deferred until upstream" Phase 13 conclusion).

---

## Reference / pinned facts

- **OBS source clone:** `reference/obs-studio/` (gitignored, shallow `--depth 1 --filter=blob:none`, ~150 MB, fetched 2026-05-19). Pin a commit SHA when ADR-0016 moves to "Implemented".
- **Veovera Enhanced RTMP v2 spec:** stable 2026-01-31. https://veovera.org/docs/enhanced/enhanced-rtmp-v2.html.
- **Production endpoint:** Unraid box at 10.10.0.2:8000.
- **Dev endpoint:** local at 10.10.0.4 (operator's dev machine).
- **GitHub:** https://github.com/pelmentor/Restream_Plus. Git author identity is repo-local (`pelmentor <cssnik2013@gmail.com>`).
- **`uvicorn workers=1` invariant locked** — single-process. KeyMaterial / RateLimiter / RepromptStore / EventBus drainer / host_stats sampler / Supervisor / LastSeenCoalescer all depend on it (ADR-0001 §"Single-process invariants").
- **`__Host-` cookie ONLY in TLS mode** (controlled by `RESTREAM_COOKIE_SECURE`).
- **`-c copy` lock** (ADR-0008) — no transcoding on our side, ever. ADR-0016 preserves it.
- **`MAX_LIFETIME_SPAWN_ATTEMPTS = 1000`** (slice 10) — absolute per-`(target,role)` spawn cap; escape requires worker rebuild via target toggle off+on.
- **OBS Enhanced-Broadcasting contract pinned** (ADR-0017, from `reference/obs-studio` commit `5dacbb6` latest stable): request `GoLiveApi::PostData` (service `"IVS"`, `schema_version "2025-01-25"`, stream key in `authentication`, GPU/CPU capabilities); response `GoLiveApi::Config` (meta+config_id, `status:"success"`, `ingest_endpoints[]` with `{stream_key}` template, non-empty `encoder_configurations[]`, `audio_configurations.live[]`). OBS encodes the returned ladder **verbatim** and **hard-fails if the named encoder `type` isn't on the client** — so the endpoint MUST pick NVENC ids from the posted GPU. One RTMP connection, all tracks muxed. Services format `version 5`. `--config-url` launch flag overrides the service URL (`GoLiveAPI_Network.cpp:111-127`).
- **Stream PC = NVIDIA RTX 5060** (NVENC H.264+HEVC+AV1). VPS-future security: stream key rides in the POST body → LAN plain-HTTP ok, but a public VPS needs Tailscale/WireGuard or Caddy+Let's-Encrypt — **never self-signed** (OBS verifies certs, no trust-cert UI).
