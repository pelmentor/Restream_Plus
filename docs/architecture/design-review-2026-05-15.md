# Design Review — 2026-05-15

Four expert subagents reviewed the initial design before code was written.
Each agent adopted a persona from `docs/prompts/` and returned a structured
critique. This document captures the **synthesized decisions** — what each
finding produced as a change to the codebase / ADRs / docs.

Reviewers (with persona prompt files):

- **Backend Architect** — `docs/prompts/engineering-backend-architect.md`
- **Software Architect** — `docs/prompts/engineering-software-architect.md`
- **ArchitectUX** — `docs/prompts/design-ux-architect.md`
- **UI Designer** — `docs/prompts/design-ui-designer.md`

The reviews are kept verbatim in the project task transcript (not in git, by
design — they would rot). What's actionable is captured below.

## Outcome at a glance

- 5 new ADRs added: 0007 (Persistence), 0008 (Worker abstraction), 0009
  (Credential model), 0010 (Headless restart), 0011 (Health & liveness).
- 3 ADRs amended: 0003 (RTMP & fan-out), 0005 (Auth), 0006 (Secret crypto).
  Smaller tweaks to 0001 and 0004.
- `design-system.md` expanded with 10+ component specs and contrast fixes.
- `ux-flows.md` expanded with `armed` state, START gating, multi-VK handling,
  slide-out enhancements, and aria/keyboard fixes.

## Decisions

Each row: **F#** is the finding (reviewer-attributed). **Decision** is
mine after synthesis. **Touches** is what was edited / added.

### Domain & architecture

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| SA-A1 | `Target` aggregate leaky for VK key + YouTube 2 workers       | Split `Target` from `Credential`; introduce `WorkerSet` per Target. | **ADR-0009**, **ADR-0003 amend** |
| SA-A2 | `RunState` / `TargetEnabled` / `WorkerState` collapse risk      | Three named state types, kept at distinct layers; documented.       | **ADR-0003 amend**, system-overview |
| SA-D2 | Worker = ffmpeg subprocess baked in                              | `Worker` becomes an abstract protocol; `FFmpegWorker` is v1 impl.   | **ADR-0008**                  |
| BA-5  | Worker state ≠ Target state, especially YouTube backup ingest   | Target-level health aggregation rules (default AND, override per type). | **ADR-0003 amend**, ADR-0008 |
| SA-A leak | Per-target log files can contain keys                        | Redaction sink invariant; covered by tests.                          | **ADR-0006 amend**, ADR-0008 |

### Ingest & supervision

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| BA-1 | Ingest key in nginx logs / `/proc/cmdline` / LAN traffic        | Tighten log redaction across access+error+debug logs; document RTMPS-from-OBS as future work; **reject** rotating-HMAC stream name (breaks OBS UX without changing the actual threat — the new value travels in the same channels). | **ADR-0003 amend** |
| BA-2 | One event loop for supervisor + API + WS is a backpressure risk | Two `TaskGroup`s with a bounded `asyncio.Queue` between them. Supervisor cannot block API.                          | **ADR-0001 amend**, **ADR-0003 amend** |
| BA-2b | Control-plane crash orphans ffmpeg children                    | Asyncio shutdown hook explicitly SIGTERMs all workers in `finally`-shape teardown; documented.                       | **ADR-0003 amend** |
| BA-D3 | Per-target circuit breaker missing                             | After N (configurable, default 30) failed reconnects within a window, target enters `failed_open` and stops retrying until user intervention.                                                                                                | **ADR-0003 amend** |
| SA-E.dup | Duplicate-publish behavior unspecified                      | `on_publish` rejects a second concurrent publish on the same ingest key; first wins.                                  | **ADR-0003 amend** |
| SA-E.buf | ffmpeg buffer flags unspecified                              | Worker invocation includes specific `-rtmp_buffer`, `-flvflags`, `-fflags +nobuffer`, `-flush_packets 1`.              | **ADR-0003 amend**, ADR-0008 |
| SA-E.locked-args | "No transcoding" unenforced                          | Worker args computed server-side from a typed struct; no user-supplied codec/encode flags accepted.                   | **ADR-0003 amend**, ADR-0008 |

### Persistence, audit, backup

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| SA-B.sqlite | SQLite choice never decided in an ADR                       | Add ADR-0007 to make the choice explicit (single writer, WAL, no migrations chain — single schema bring-up).         | **ADR-0007** new              |
| SA-E.audit | Audit log too broad for single-user                          | Keep: auth events, run start/stop, password/passphrase change, API token lifecycle, target *secret-modified* events. Drop: every-CRUD event noise. | **ADR-0007**                  |
| BA-D1 | Audit log durability under control-plane crash                  | Audit writes are synchronous; SQLite WAL `synchronous=NORMAL`; on restart with active session, recover-and-record an `interrupted_session_recovered` event. | **ADR-0007**, ADR-0003 amend |
| BA-D2 | Backup/restore procedure & passphrase rotation timeline         | Documented procedure: export → backup → restore = preserved if same passphrase. Passphrase rotation is stop-the-world AND backs up the previous KDF salt for 24h of grace. | **ADR-0006 amend**, ops/backup-restore.md (to write later) |
| BA-4 | AAD coupled to `target_id` breaks restore-after-delete           | Drop `target_id` from AAD. Per-record random salt + fixed type discriminator (`stream_key:v1`).                       | **ADR-0006 amend** |

### Auth & crypto

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| BA-3 | In-memory rate-limit / session cache lost on restart            | Drop session cache (SQLite handles it). Rate-limit state stays in-memory; volatility called out as accepted trade.   | **ADR-0005 amend** |
| SA-E.mac-key | `session_mac_key` HKDF derivation unused                  | Drop it. Two HKDF derivations: `stream-key-wrap-v1` and `api-token-mac-v1`.                                          | **ADR-0006 amend** |
| SA-E.cache | Session DB cache premature                                   | Removed. SQLite easily handles per-request session lookup at our scale.                                              | **ADR-0005 amend** |
| SA-C.rot | Passphrase rotation is stop-the-world (not as reversible as claimed) | Documented: rotation requires run-state stopped, blocks new starts.                                              | **ADR-0006 amend** |
| SA-B.passphrase, SA-E.headless | No headless-restart story                       | Add ADR-0010. v1 default: passphrase from env var (`RESTREAM_MASTER_PASSPHRASE`); v1 alternative: paste-on-start via local-only HTTP form; v2: TPM/keyring (deferred). | **ADR-0010** new |

### Operations

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| SA-E.health | No `/health` contract                                        | Add ADR-0011. Endpoints: `/livez` (always 200 if process up), `/readyz` (DB writable + nginx reachable + supervisor loop ticking).                                | **ADR-0011** new              |
| SA-C.image | Single-image not as reversible as claimed                     | Sharpen ADR-0004 language. Removing s6 / loopback assumption = major refactor, not "swap one decision".               | **ADR-0004 amend** |
| SA-C.anyio | Asyncio→anyio swap not local                                  | Sharpen ADR-0001 — swap is local to subprocess spawn but supervisor module would not need rewriting if we'd kept abstractions clean from day one. ADR-0008 does that. | **ADR-0001 amend** |

### UI/UX

| F# | Finding                                                            | Decision      | Touches                       |
| -- | ------------------------------------------------------------------ | ------------- | ----------------------------- |
| UI-A.contrast | warn-on-warn-faint / dark error-on-error-faint / dark live-on-live-faint / focus on accent-faint all fail AA | Deepen tokens; add explicit "semantic-strong on semantic-faint pairs must be independently verified" rule. | **design-system.md update** |
| UI-A.typescale | 2xl→3xl off-ratio, 2xl line-height loose                   | 3xl drops to 28 px; introduce `--text-display-sm` for 32 px; 2xl line-height tightens to 1.25.                       | **design-system.md update** |
| UI-A.space-10 | Missing `--space-10: 40px`                                 | Added.                                                                                                                | **design-system.md update** |
| UI-B + UX-D.components | Missing primitives: SecretField, CopyToClipboard, Sparkline, LogViewer, DestructiveConfirm, OneTimeRevealBanner, InlinePromptCard, Skeleton, ReconnectingBanner, Slider, Banner (persistent), MetricGrid, AuthReprompt, RecentEventsMenu | Specced into `design-system.md` §6 as components 6.9–6.21. | **design-system.md update** |
| UI-C.start-width | Hero button width unbounded                              | 280–420 px.                                                                                                          | **design-system.md update** |
| UI-C.live-ambient | No ambient affordance during live                       | Hairline border tick on the hero card (1 s, 120 ms transition); badge dot pulse at 1.5 s. Both off under reduced-motion. | **design-system.md update** |
| UI-C.danger-flip | Variant flip needs more than label change                 | 200 ms cross-fade through neutral on primary↔danger transition.                                                       | **design-system.md update** |
| UI-C.confirm-on-stop | Choreography unspecified                              | STOP morphs in place to a 2-pill `Stop · Cancel` segmented control for 3 s; reverts on timeout or pointer-leave.      | **design-system.md update**, **ux-flows.md update** |
| UI-D.states | Component-state matrices incomplete                        | Filled in per UI-Designer's table.                                                                                    | **design-system.md update** |
| UI-E.spring-scope | Spring on color transitions risky                      | Spring restricted to press/release on the hero button only.                                                          | **design-system.md update** |
| UI-E.theme-svg | Unicode glyphs render inconsistently                      | Replace with Phosphor SVG icons in v1.                                                                               | **design-system.md update** |
| UX-A.badge-from-settings | Click behavior from `/settings` undefined         | Click on badge from any non-`/` route navigates to `/`; from `/` it scrolls to hero.                                  | **ux-flows.md update**       |
| UX-A.events-menu | No persistent surface for transient errors             | Add `RecentEventsMenu` to the header (last 50 events of current session, in-memory, links to slide-outs).             | **design-system.md update**, **ux-flows.md update** |
| UX-A.version-surface | Build SHA buried in About                          | Add small version badge in account dropdown and in the page footer.                                                  | **ux-flows.md update**       |
| UX-A.settings-breadcrumb | No "back to dashboard" from Settings              | Sidebar header gets a "← Dashboard" link.                                                                            | **ux-flows.md update**       |
| UX-A.session-history | No place for past sessions                          | Read-only `Settings → Sessions` tab; last 50 sessions, timestamps, per-target outcome.                                | **ux-flows.md update**       |
| UX-A.logs-path | Full log retention has no UI surface                   | About page documents path `/data/logs/...`. v1 accepts shell-only access.                                            | **ux-flows.md update**       |
| UX-B.first-run | New user misses OBS-config dependency                  | 3-step inline hint on first-ever Dashboard load, dismissed after first successful go-live; persisted as a boolean in DB. | **ux-flows.md update**       |
| UX-B.start-gating | START enabled with 0 enabled targets is misleading    | START disabled with tooltip "Add at least one enabled target". Empty-state replaces the hero card.                    | **ux-flows.md update**       |
| UX-B.armed | `armed` state missing                                        | Added: `offline → starting → armed → live (when OBS connects) → stopping`. Status `armed` shown with a "Waiting for OBS" banner. | **ux-flows.md update**, **ADR-0003 amend** |
| UX-B.multi-vk | Multiple VK targets in one paste-card unspecified         | Inline card stacks one input per enabled VK target, labelled with the target's name.                                  | **ux-flows.md update**       |
| UX-B.vk-affordances | Validation / docs link / copy wording               | Inline "looks valid / looks malformed" hint based on length+format; "Where do I find this key?" link.                 | **ux-flows.md update**       |
| UX-C.slideout | Details panel is bare                                    | Adds: copy-log, download-log, pause-tail, error-since timestamp, retry count, "Retry now", "Disable just this target" with scope-explicit copy. | **ux-flows.md update**       |
| UX-E.aria-pressed | Wrong semantic on START/STOP                          | Drop `aria-pressed`. Rely on label change + single `aria-live="polite"` region near the badge.                       | **ux-flows.md update**, **design-system.md update** |
| UX-E.double-live | Two live regions announce same state                    | One live region in the badge; remove from the button.                                                                | **design-system.md update** |
| UX-E.theme-aria | Emoji-only aria announcements                          | Explicit `aria-label="Light theme"` etc. on each toggle option.                                                       | **design-system.md update** |
| UX-F.focus-slideout | Slide-out focus management unspecified              | On open: focus moves to panel close button. On close: focus returns to the activating tile. Esc closes.               | **design-system.md update** |
| UX-F.auth-reprompt | "Password re-prompt" referenced but not specced       | New component `AuthReprompt` — small modal, current password only, 60 s validity for the granted action.              | **design-system.md update** |
| UX-F.messages-ts | Hardcoded strings without a future-i18n seam            | Add `src/messages.ts` from day one as the single string source. i18n itself stays out-of-scope.                       | **frontend scaffold note**   |

## What got rejected or accepted with modification

- **BA-1 rotating HMAC**: rejected as proposed. Rotating the stream-name forces OBS users to reconfigure every rotation; meanwhile the rotating value travels in the same channels (logs, cmdline). The real fix is log discipline + RTMPS-from-OBS (future work in ADR-0003).
- **SA-E.session-cache**: accepted (drop). I had originally argued for it to reduce SQLite churn; SA pointed out SQLite easily handles it. Premature optimization.
- **SA-E.audit-trim**: accepted with modification. I keep slightly more than SA suggested — I keep "secret modified" events because they're rare and high-signal for forensics.

## What got reinforced (no change)

- Single image with s6-overlay. Both architects flagged its "reversibility" claim as too generous; the choice itself stands.
- nginx-rtmp as ingest (vs MediaMTX). Trade-off rationale in ADR-0003 sharpened; choice stands.
- Argon2id + opaque server-side sessions over JWT. Both architects endorsed.
- VK paste-on-start (not in Settings). All four reviewers endorsed.
- Two routes total. UX architect endorsed; no expansion.

## Process note (per project Rule №3)

This review was triggered by the project owner saying: "Про какие технологии лучше использовать и как проектировать проект тоже спроси агентов, пусть дадут идеи инсайты, используй скачанные промпты и правила наши." Translated: spawn agents for tech and design choices, not just unknowns; brief them with the downloaded persona prompts and our rules.

The pattern worked. Four agents in parallel, each took ~80–110 s, returned
~800–1100 words of structured critique, and surfaced two architectural seams
(Worker abstraction, Credential lifecycle) plus a Domain rename that I had
missed despite spending real time on the initial ADRs. Rule №4 (post-coding
review agents) will be invoked similarly after each implementation layer
lands.
