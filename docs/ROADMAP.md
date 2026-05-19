# Restream_Plus Roadmap

**Last updated:** 2026-05-19
**Purpose:** single-page grand plan. Cold-resume entry point. Everything
else (`SESSION_HANDOFF.md`, ADRs, audit burndowns) is reachable from
here. If you're picking up this project after a compact / context loss /
fresh conversation, read this first.

---

## Current state

- **Production:** `ghcr.io/pelmentor/restream-plus:v1.1.3` running on operator's Unraid box (10.10.0.2:8000, plain HTTP, bind-mount `/mnt/user/appdata/restream-plus`). Five releases shipped 2026-05-18 (v1.0.0 → v1.1.3); **every release before v1.1.3 has a known browser-blocking bug — do NOT pull `:v1.0.0` through `:v1.1.2`**.
- **LOCAL-DEV MODE:** no GHCR image builds or tag pushes without explicit operator authorisation. `build-image.yml` only fires on push to `main`, so merging into `main` triggers `:edge` + `:sha-<short>` automatically.
- **Hex audit:** terminal state. Both burndown files at 0 open findings (`docs/audit/hex-audit-2026-05-18.md` 87/0, `docs/audit/hex-audit-2026-05-19.md` 31/0). Future regressions need a fresh hex-audit run.
- **Two open PRs awaiting operator dev-machine test:**
  - [PR #14 — `refactor(audit): drain hex-audit burndown — slices 8/8.5/9/10`](https://github.com/pelmentor/Restream_Plus/pull/14). Base `main`. 133 files, +12791/−1249.
  - [PR #15 — `feat(run): auto-run-on-publish — OBS publishing is the only run trigger`](https://github.com/pelmentor/Restream_Plus/pull/15). Stacked on #14 (base = `audit/hex-burndown-slices-8-to-10`). 18 files, +636/−771. GitHub auto-flips base to `main` once #14 merges.

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

## Multi-track / Enhanced RTMP support (ADR-0016)

**Design accepted, no code written, implementation gated on Phase A pilot.**

See [ADR-0016](architecture/ADR-0016-enhanced-rtmp-multitrack-ingest.md) for the full architecture (always-on `FlvDemuxer` with single-track fast path, ffmpeg-tap (Option A), stdin-pipe distribution to per-target workers, `settings_json["track_preference"]` per target, fail-loud codec compat). See [docs/research/obs-multitrack-feasibility.md](research/obs-multitrack-feasibility.md) for the OBS source-level evidence behind the design.

**Why this matters:** OBS encodes a `{1080p/720p/480p}` ladder once; Restream_Plus picks the right rendition per target. Today every target gets the same single-track stream regardless of platform fit.

### Phase A — Pilot (≤1 day, operator-driven, MANDATORY before Phase B)

**No application code changes.** Operator-facing setup + analysis script.

See [docs/ops/multitrack-pilot.md](ops/multitrack-pilot.md) for the procedure. Exit gates:

- [ ] OBS with `multitrack_video_config_override` JSON actually emits `PACKETTYPE_MULTITRACK = 6` tags via the `custom_config_only` path.
- [ ] ffmpeg `-c copy -f flv` tap forwards multi-track bytes intact (does NOT silently drop them).
- [ ] Tag layout matches [reference/obs-studio/plugins/obs-outputs/flv-mux.c:442-501](../reference/obs-studio/plugins/obs-outputs/flv-mux.c#L442) exactly.
- [ ] JSON schema for `multitrack_video_config_override` empirically validated.
- [ ] Captured FLV fixture committed at `tests/fixtures/multitrack_pilot.flv` (or SHA-pinned in CI fixture spec if too large).

**If item 2 fails:** ffmpeg tap is broken for multi-track; escalate to Option B (Python RTMP client) in Phase B; adds ~2 weeks scope.

### Phases B → E (sequential, only after Phase A passes)

| Phase | Scope | Time |
|---|---|---|
| **B** | `FlvDemuxer` + `FlvTap` + `SequenceHeaderParser` modules + unit tests; `WorkerSpec.stdin_provider`; `TargetTypeSpec.accepted_video_codecs`; `TrackManifestEvent` bus type | ~1 week |
| **C** | Supervisor wiring (`_start_run_locked` tap startup, `_stop_run_locked` tap teardown, track-aware `_spawn_workers_for_target`); ffmpeg-worker stdin-writer task; `settings.track_preference` read path. **Exit:** Twitch + YouTube each receive auto-selected rendition from 2-track OBS push | ~1 week |
| **D** | `TrackSelector.tsx` + `useTrackManifest.ts`; per-target track-preference UI in `TargetDetails.tsx`; API validation | ~3 days |
| **E** | Full SPS/VPS/OBU resolution parsing; demuxer Hypothesis fuzz; long-session memory profile; codec-compat coverage; track-disappeared-mid-session test | ~3 days |

**Total impl scope post-pilot:** ~3 weeks of work + reviewer + audit per Rule №4/№5.

---

## Deferred / future / non-blocking

- **Twitch Multitrack downstream forwarding** — out of scope per ADR-0016 §5 (proprietary API, RTX-only HEVC beta). Future ADR-0017 amendment if Twitch publishes the partner API; until then Twitch gets one track on its standard ingest, same as everyone.
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
