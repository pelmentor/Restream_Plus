# Phase 8 — Frontend Dashboard design memo

Synthesizes the three agency-agent passes (UX Architect, UI Designer,
Software Architect) into one locked set of decisions. Each section
below is binding for Phase 8 code; any deviation requires an explicit
amendment, not a quiet skip.

**Inputs the three agents read (and cited):** `docs/ui/ux-flows.md`,
`docs/ui/design-system.md`, `docs/architecture/phase-7-design-memo.md`,
the Phase 7 §"what landed" section of `docs/SESSION_HANDOFF.md`, the
backend wire shapes in `app/api/schemas.py`, `app/api/run.py`,
`app/api/targets.py`, `app/api/ws.py`, and the domain enums in
`app/domain/*.py`.

---

## A. Backend prerequisite — `RunStateView.run_state_changed_at`

The agents converged: a wall-clock timer for "LIVE 00:17:42" needs the
moment the run last entered its current state. Neither
`heartbeat_age_seconds` nor the per-event `at` field on
`run.state.changed` survives a hard reload mid-LIVE.

**Lock:**

- Add `run_state_changed_at: AwareDatetime | None` to `RunStateView`
  (`app/api/schemas.py`).
- Track on `Supervisor`: bump in lockstep with the existing
  `_set_run_state` call sites.
- Surface in both `GET /api/run/state` and the WS `state.full` payload
  via the existing `_build_state_full`.
- Tests: assert the field is None when supervisor has never run,
  non-None after first start, and monotonically bumped on each
  transition.

**Why not derive client-side from `run.state.changed.at`:** that field
covers live transitions, not cold-boot / reconnect. UX Architect Q12 +
Software Architect Q7 agree this is option A territory (Rule №1 — no
quick fixes).

## B. Data flow — TanStack as single source of truth

**Lock:** TanStack QueryCache is the only run-state / target store. The
WS hook patches via `setQueryData` for two well-known keys:
`["run","state"]` and `["targets"]`. No parallel `useSyncExternalStore`
live-state store.

Rationale (Software Architect Q1):
- One mental model; Phase 7 already uses TanStack for
  `["auth","session"]`.
- Devtools, retry policy, `gcTime` come for free.
- The mapping surface is tiny — 4 WS events × 2 keys; lives in one file
  (`hooks/useStatusStream.ts`).
- `useStatusStream` is the **sole writer** of these keys outside their
  own `queryFn`. This is documented and contained.

## C. Query keys (locked vocabulary)

| Key | Owner | Notes |
|---|---|---|
| `["auth","session"]` | Phase 7 | `staleTime: Infinity` |
| `["run","state"]` | Phase 8 | `staleTime: Infinity` (WS owns freshness; REST is cold-boot seed) |
| `["targets"]` | Phase 8 | `staleTime: 30s` (TanStack default); mutations invalidate |
| `["settings"]` | Phase 8 (read-only here) | Used for `first_run_complete` + the masked ingest key context |

No per-id key for targets in Phase 8 — the slide-out reads from the
list cache. The appliance use case has ≤ ~10 targets; O(N) array
filtering is free.

## D. Hook surface (locked signatures)

```ts
// hooks/useRunState.ts
function useRunState(): {
  runState: RunState;
  heartbeatAgeSeconds: number;
  droppedTotal: number;
  runStartedAt: Date | null;    // null when runState !== "live"
  isPending: boolean;
}

// hooks/useTargets.ts
function useTargets(): {
  targets: ReadonlyArray<TargetWithSnapshot>;
  isPending: boolean;
}
type TargetWithSnapshot = TargetT & { snapshot: TargetSnapshotT | null };

// hooks/useStatusStream.ts (cache patcher + status reader split)
function useWsConnectionStatus(): { connectionStatus: WsConnectionStatus };
// (The single subscriber that patches the cache lives inside
//  <StatusStreamHost>, not in a public hook.)
```

`useRunState` derives `runStartedAt = runState === "live" ? run_state_changed_at : null`. The HeroCard's timer renders via a 1s
`setInterval` gated on `runState === "live"`.

## E. Zod schemas — module layout

```
web/src/lib/schemas/
├── _shared.ts        (Phase 7 — AwareDatetime helper)
├── auth.ts           (Phase 7)
├── health.ts         (Phase 7)
├── run.ts            (NEW — RunState, TargetUiState, WorkerState, WorkerRole,
│                            WorkerSnapshotView, TargetSnapshot, RunStateView,
│                            RunActionAcceptedResponse)
├── targets.ts        (NEW — TargetType, Target, CredentialSummary)
├── settings.ts       (NEW — SettingsView, read-only)
└── wsEnvelopes.ts    (NEW — per-event-type data schemas + WsKnownEvent
                              discriminated union)
```

Conventions: T-suffix on types (e.g. `RunStateT`, `TargetT`). Enums are
`z.enum([...])` mirroring the backend `Enum.value` strings exactly.

`WsKnownEvent` is the discriminated union over the four known
`event:` strings (`state.full`, `run.state.changed`, `target.snapshot`,
`bus.drop_alert`). Unknown events parse-fail and are dropped silently
(forward-compat).

## F. WS → cache patching

**Lock:** every WS event's `data` is Zod-validated before reaching the
cache. The cost (microseconds per envelope at ≤10 envelopes/sec) is
trivial; the cost of a single bad envelope silently corrupting
`["run","state"]` is much higher.

Mapping (single file `hooks/useStatusStream.ts`):

| WS event | Cache mutation |
|---|---|
| `state.full` | `setQueryData(["run","state"], data)` — full replace |
| `run.state.changed` | Patch `run_state` + `run_state_changed_at` (using `at`) |
| `target.snapshot` | Patch matching target's `snapshot` in array; append on race |
| `bus.drop_alert` | Patch `dropped_total` |

All patches use `prev => prev ? patch(prev) : prev` to tolerate the
"seed not yet present" race.

**The patcher is extracted as a pure function** `applyWsEventToCache(prev: RunStateViewT | undefined, event: WsKnownEventT): RunStateViewT | undefined` and tested in isolation under Vitest
(see §M). The hook becomes a thin `setQueryData(key, prev => applyWsEventToCache(prev, event))` wrapper.

## G. Mutations

| Mutation | Endpoint | `silenceGlobalErrors` | Optimistic? | Invalidations |
|---|---|---|---|---|
| `start` | `POST /api/run/start` (202) | **No** | No | None — server's WS event refreshes |
| `stop` | `POST /api/run/stop` (202) | No | No | None — same |
| `setCredential` | `PUT /api/targets/{id}/credential` | No | No | `["targets"]` |
| `resetWorker` | `POST /api/targets/{id}/reset-worker?role=...` | No | No | None — `target.snapshot` event |

**Why no optimistic on start/stop:** the 202 → WS round-trip is
sub-second; the HeroCard surfaces pending state via
`mutation.isPending`. An optimistic flip would need a rollback path for
the rare `UNRECOVERABLE_FAILED` case. Letting WS drive the badge
eliminates the rollback class entirely.

**409 `illegal_run_state`:** treat as "your view is stale" — invalidate
`["run","state"]` and surface `<Banner variant="info">` with copy
`dashboard.staleRunState`. Do not retry.

**Auth on mutations:** stays on the bus (no opt-out). If the cookie
expired server-side, the global `GlobalErrorHandler` routes to
`/login` — that is the desired UX.

## H. RunStateBadge — replaces the Phase-7 stub

**Lock:** the Phase 7 `<RunStateBadgeStub>` JSX slot in
`AppShell.tsx::Header` is replaced **in place**. The wrapping
`<div role="status" aria-live="polite">` (or its `<button>`
equivalent — see below) is preserved verbatim — Phase 7's load-bearing
contract (the single live region for run-state announcements,
F# UX-E.double-live).

**Visual states + live-text (UI Designer Q1):**

| RunState | Dot color | Pulse | Pill bg | Text color | Label | Icon |
|---|---|---|---|---|---|---|
| `offline` | `--color-fg-muted` | off | `--color-bg-elevated` | `--color-fg-muted` | `OFFLINE` | none |
| `starting` | `--color-accent` | off — replaced by spinner | `--color-accent-faint` | `--color-accent` | `STARTING…` | `CircleNotch` spinning |
| `stopping` | `--color-error` | off — replaced by spinner | `--color-error-faint` | `--color-error` | `STOPPING…` | `CircleNotch` spinning |
| `armed` | `--color-warn` | off | `--color-warn-faint` | `--color-warn` | `ARMED` | none |
| `live` | `--color-live` | on, 1.5s opacity cycle | `--color-live-faint` | `--color-live` | `LIVE 00:17:42` (mono tabular) | none |
| `error` | `--color-error` | off | `--color-error-faint` | `--color-error` | `ERROR` | `WarningCircle` |

**Aria-label** is the contextual rephrase ("Live for 17 minutes",
"Offline", "Error — open dashboard for details") per ux-flows §1.

**Click behavior** (F# UX-A.badge-from-settings): outer wrapper is a
`<button type="button">` with `aria-live="polite"` directly on it. On
`/` → scrolls `#dashboard-hero` into view + focuses the START/STOP
button. On any other pathname → `navigate("/")` then Dashboard mount
effect scrolls + focuses.

**Live timer** is rendered inside the badge at the native `--text-2xs`
size, mono + tabular numerals (UI Q13). The hero card does NOT
duplicate the timer (single source of truth, design-system §9). The
48px `--text-display` token is reserved for a future presenter view
(out of scope).

**Live pulse keyframe** lives in `tokens.css`. The existing
`prefers-reduced-motion` clamp (animation-iteration-count: 1)
correctly halts the pulse after one cycle — no extra rule needed.

## I. HeroCard layout per RunState

**Container:** `mx-auto w-full max-w-[720px] rounded-(--radius-xl) bg-(--color-bg-elevated) border border-(--color-border-subtle) shadow-(--shadow-sm) p-(--space-8)`. ID `dashboard-hero` for badge scroll-target.

| State | Button | Body |
|---|---|---|
| `offline` | `START` (primary xl) | Ingest panel: `Push OBS to:` + URL (mono, copy) + masked stream-key field. First-run hint stacked when applicable (§J). |
| `starting` | `STARTING…` (primary xl disabled, spinner) | Muted: `Spawning workers…` |
| `armed` | `STOP` (danger xl) | `<Banner variant="info">` inside the card: `Waiting for OBS to start streaming` |
| `live` | `STOP` (danger xl). 1px outer ring `--color-live-faint` (design-system §6.3 live-ambient) | One-line glanceable summary `Live to {n}/{total} targets · {totalBitrate} Mbps · drops {totalDrops}` (mid-dot separators muted). |
| `stopping` | `STOPPING…` (danger xl disabled, spinner) | Muted: `Draining workers…` |
| `error` | `START` (primary xl, `ring-1 ring-(--color-error)`) | `<Banner variant="error">` with redacted summary + link button to open the first failed tile. |

**Resolved CONFLICTs (logged here so the doc reader doesn't re-litigate):**

- ux-flows §2 LIVE body shows `Ingest healthy · 6.2 Mbps · 60 fps · drops 0`. The backend `RunStateView` has no ingest-side
  metric (per `app/api/schemas.py`). **Resolution:** Phase 8 renders
  aggregate target metrics instead (mean bitrate across RUNNING
  targets, sum drops, count). Single-line, glanceable. Ingest metrics
  are a documented Phase-9-or-later backlog item.
- ux-flows §2 VK flow step 5 says keys are sent "in the start payload".
  Backend has no such payload (`POST /api/run/start` body is empty).
  **Resolution:** PUT credential first, then POST start (see §J).
  Update ux-flows.md in a doc-pass follow-up.
- The hero `offline` ingest URL is derived client-side as
  `rtmps://${window.location.host}/live` (the loopback-only RTMP is
  inside the container; the user-facing ingest is the same hostname as
  the panel). Plaintext key reveal is **deferred to Phase 9** — Phase 8
  renders the masked field with a "Reveal in Settings" link rather
  than building a reveal endpoint + AuthReprompt flow now.

## J. VK Live mid-flow paste (InlinePromptCard)

**Lock the click-handler decision tree:**

```ts
const enabledTargets = targets.filter(t => t.enabled);
const vkNeedingKey = enabledTargets.filter(
  t => t.type === "vk_live" && !t.has_credential
);

if (enabledTargets.length === 0) return <EmptyState ... />;  // hero hidden

function onStartClick() {
  if (vkNeedingKey.length > 0) {
    setVkPromptOpen(true);
    return;
  }
  startMutation.mutate();
}
```

**Use & start:** PUT each VK credential, invalidate `["targets"]`, POST start.
**Skip this time:** POST start directly. The backend supervisor's
`_credentials_satisfied` gate (`app/domain/target.py:121`) routes
credential-less VK targets to `DISABLED_MISCONFIGURED` automatically —
no special server-side "skip" path needed. After STOP, persistent
`enabled` flag is untouched (per F# UX-B.vk-affordances).

**Visual:** Inset card under the hero with `--color-warn-faint` bg +
4px `--color-warn` left border (design-system §6.15). One paste-only
input per VK target, stacked, labelled by user name. Passive
"looks valid" / "looks malformed" hints (informational only). Buttons:
`Use & start` primary `lg` (NOT `xl` — `xl` is hero-only per
F# UI-C.start-width), `Skip this time` ghost `lg`.

## K. Confirm-on-stop choreography

Per design-system §6.1 + UX Q5 + UI Q3.

- On STOP click: single button morphs into 2-pill segmented control
  `[Stop] [Cancel]` inside the same `h-20` frame.
- Text scale drops to `--text-xl` inside the morph (two pills don't fit
  display-sm).
- 3s auto-revert (or pointer-leave on the group).
- Focus moves to `[Stop]` pill on morph; Arrow Left/Right cycles
  between the two pills; Escape == Cancel.
- `<div role="group" aria-label="Confirm stop, 3 seconds to cancel">`;
  no `aria-pressed` on either pill (F# UX-E.aria-pressed).
- Variant flip (`primary` ↔ `danger`) uses `--motion-flip` (200ms) on
  background-color/color/box-shadow only.
- Press/release spring (`scale(0.97)`) uses `--motion-spring` and is
  bound exclusively to `size="xl"` per F# UI-E.spring-scope.
- Reduced-motion: global 16ms clamp + the variant flip becomes
  effectively instant. No override needed.

## L. Target tile + slide-out

**Tile** is a single `<button type="button" aria-expanded={isOpen} aria-controls="slideout">` with the whole card surface as the
keyboard target. Header row: platform icon (Phosphor) + label + status
pill. Metrics row: `MetricGrid` (§N below). No nested buttons.

**Status icons: Phosphor only.** design-system §6.3's
unicode ●/◐/✕/⚠ is mockup notation; Phase 7's Phosphor-only commitment
(F# UI-E.theme-svg) extends here for the same cross-OS-rendering
reason that drove the theme-toggle change. Doc-pass follow-up to
update §6.3.

| TargetUiState | Border | Pill bg/text | Pill label | Icon |
|---|---|---|---|---|
| `DISABLED` | subtle | `--color-bg-sunken` / `--color-fg-muted` | `DISABLED` | `Prohibit` |
| `DISABLED_MISCONFIGURED` | `--color-warn` 1px | warn-faint / warn | `NO KEY` | `Warning` |
| `IDLE` | subtle | `--color-bg-sunken` / default | `ENABLED` | `Circle` |
| `STARTING` | `--color-accent` 1px | accent-faint / accent | `STARTING` | `CircleNotch` spinning |
| `RUNNING` | `--color-live` 1px | live-faint / live | `LIVE` | `Circle` fill |
| `DEGRADED` | `--color-warn` 1px | warn-faint / warn | `DEGRADED` | `WarningCircle` |
| `ERRORED` | `--color-error` 1px | error-faint / error | `RECONNECTING` (with countdown `RETRY 4s` when known) | `ArrowsClockwise` spinning |
| `FAILED_OPEN` | `--color-error` 2px | error-faint / error | `STOPPED` | `XCircle` fill |

**Slide-out** (Radix Dialog `modal={false}` per design-system §6.4):

- Desktop: `right-0 w-[480px]` with `border-l --color-border-subtle` +
  `--shadow-lg`.
- Mobile (`<640px`): full-screen, no border, back-arrow close instead
  of `X`. Same component, breakpoint-driven CSS — NOT a separate
  `MobileTargetDetails`. (UX Q14, UI Q8).
- Header strip is `--space-10` tall (F# UI-A.space-10).
- Body: Sparkline → MetricGrid → error context → LogViewer → action row.
- **Focus management** (F# UX-F.focus-slideout):
  - On open: focus moves to the close button (override Radix's default
    onOpenAutoFocus).
  - Tab order inside: close → Retry now → Disable just this target →
    Edit settings → Pause-tail → Copy all → Download .log.
  - Escape closes; close returns focus to the activating tile.
- Slide animation: `translateX(100%)` → `translateX(0)` via
  `--motion-default`. Reduced-motion: opacity-only fade
  (`motion-reduce:translate-x-0 motion-reduce:transition-opacity`).
- **URL-backed state** via `?target=<id>` (UX Q11, SA Q8).
  `useSearchParams` reads the value; unknown id silently closes (do
  not throw). Browser back closes the slide-out.

## M. Pure-function WS reducer + Vitest

**Lock:** add Vitest as a dev dep for Phase 8, narrow scope.

- Test target: `applyWsEventToCache(prev, event)` — a pure function
  with no React / no DOM / no jsdom. Vitest config: `environment: "node"`.
- Why: the WS-to-cache reducer is the highest-risk single piece of
  code in Phase 8. Lint + typecheck don't catch logic bugs. A
  regression here ships wrong state directly to the user.
- No `@testing-library/react` yet. Component tests deferred to a
  future Phase 8b. Floor: `typecheck && lint && build && test` all
  pass.
- Scripts: `npm run test` added to package.json. Bundle-size script
  unchanged (post-build only).

## N. Reusable components — visual locks

Tokens-only; AA-verified semantic-strong-on-faint pairings from
design-system §1.

- **`Banner`** (Phase 7 already): info / warn / error. Used both as
  page-level chrome (`ReconnectingBanner` — info variant) and inside
  the HeroCard (armed / error states).
- **`Skeleton.Tile` / `Skeleton.Row` / `Skeleton.Pill`** (Phase 7
  already): cold-boot loading state for the tile grid + hero.
- **`MetricGrid`** (NEW): `grid-cols-2 gap-x-(--space-4) gap-y-(--space-2)` tabular. Label
  `--text-2xs` muted, value `--text-sm` mono strong. Units rendered
  inside the value, muted + sans. Long values truncate.
- **`Sparkline`** (NEW): 240×48 CSS px canvas, scaled by DPR. Reads
  resolved CSS custom property values directly (no `hsl(...)`
  wrapping — per Phase 7 source-form deviation, tokens.css ships full
  `hsl(...)` values per §D of Phase 7 memo). Re-reads tokens via a
  `MutationObserver` on `data-theme` + a `matchMedia` listener for
  system mode — NOT every redraw. Reduced-motion: straight lineTo
  segments (no bezier smoothing), 1Hz updates unchanged (data
  freshness is functional, not decorative). Accessibility:
  `aria-label="Bitrate over the last 5 minutes"` + a throttled sr-only
  text summary.
- **`LogViewer`** (NEW): mono `--text-sm`, `--color-bg-sunken` body,
  `--color-bg-elevated` header strip. 20-row max-height (`max-h-[450px]`)
  in slide-out. Auto-scroll detection with an 8px threshold;
  programmatic scrolls bypassed via a `programmaticScroll` flag.
  Header tools: Pause tail / Copy all / Download .log (ghost
  buttons).
- **`InlinePromptCard`** (NEW): inset under hero, `--color-warn-faint`
  bg + `--color-warn` 4px left border. Paste-only inputs (Phase 8
  minimal variant — NOT depending on the full `SecretField` from
  Phase 9). Buttons: `Use & start` primary lg, `Skip this time` ghost
  lg.
- **`ReconnectingBanner`** (NEW, info variant): in-flow at the top of
  `<main>`, NOT sticky, NOT in the AppShell header. Persistent (no
  dismiss). Appears on the first failed reconnect attempt — momentary
  blips don't warrant chrome. Disappears on `open` status.
- **`RecentEventsMenu`** (NEW): bell icon in AppShell header with 6px
  unseen-count dot (`box-shadow: 0 0 0 2px var(--color-bg-base)` ring
  for contrast). 320px dropdown, max-height 480px, scrollable list +
  fixed "Clear" footer.
- **`ErrorBoundary`** (NEW, in-house ~30 lines): applied at
  `<TargetDetails>` body level + `<Sparkline>` specifically. NOT at
  the Dashboard route level (a corrupted cache should hard-fail in
  dev). No new dep (`react-error-boundary` rejected — Phase 7
  established the "no new deps for small things" pattern).
- **`HeroCard`, `TargetTile`, `TargetDetails`, `RunStateBadge`,
  `RecentEventsMenu`**: new Phase 8 components per the §H/I/L/N
  visual locks.

## O. RecentEventsMenu store

**Lock:** React Context + `useReducer` reducer, mounted in `<AppShell>`.

- Shape: `{ events: RecentEvent[]; unseenCount: number }` capped at
  50; oldest drops on push.
- Edge-triggered: keep a `prev` map for `target.snapshot.ui_state` so
  we log only transitions to `ERRORED` / `FAILED_OPEN` (and recoveries
  from them to `RUNNING`). Snapshot-every-second at `RUNNING` is NOT
  logged.
- `run.state.changed → error` → ERROR row. `state.full / run.state.changed → live` after a prior error → RECOVERY row.
  `bus.drop_alert` → WARN row.
- The `useStatusStream` patcher dispatches `push` actions in addition
  to writing the cache. Bell opens → `markSeen` resets unseenCount;
  the "Clear" button clears the buffer too.
- Not persisted; memory-only (matches design-system §6.21).

## P. Toast vs RecentEventsMenu — no double announcement

**Lock:** target ERROR events go to `RecentEventsMenu` ONLY. NO toast
for system events.

- Toasts are reserved for ephemeral user-action feedback (e.g.
  "Credential saved", "Target disabled"). NEVER for WS-derived system
  events.
- The tile itself is the primary surface for a target ERROR (red pill,
  red border). A toast would be redundant signal.
- The `aria-live` collision risk (toast region + badge region both
  speaking the same error) is the real reason this matters.

## Q. First-run hint

**Lock:** read `settings.first_run_complete` from `GET /api/settings`;
treat the hint as dismissed if EITHER that flag is true OR a
`localStorage["restream-plus.firstRunHintDismissed"]` flag is true. On
the first `run.state.changed` where `new_state === "live"`, set the
localStorage flag and re-render (the hint fades out via
`--motion-default`).

The backend currently has no handler that flips
`first_run_complete` post-LIVE (verified — `app/db/schema_init.py:177`
only sets it once at boot). Phase 9 will add the proper backend hookup
(either `SettingsUpdateRequest.first_run_complete` or a dedicated
`POST /api/settings/mark-first-run-complete`); until then the
localStorage flag is the deliberate Phase-8 owner of "dismissed" —
acceptable in the single-admin-single-device threat model.

**Visual:** `--color-info-faint` inset card with 2px `--color-info`
left border. Numbered circles (NOT Phosphor icons — three-step lists
are a number-list pattern). Strings: `dashboard.firstRunStep1/2/3`.

## R. Lifecycle — `<StatusStreamHost>`

**Lock:** the WS lives inside a dedicated `<StatusStreamHost />`
component mounted as a **sibling of `<AppShell />`** inside
`<RequireAuth>`:

```tsx
<RequireAuth>
  <StatusStreamHost />     {/* empty render; owns connect/disconnect + WS-to-cache patching */}
  <AppShell />              {/* header + main + footer */}
</RequireAuth>
```

Why:
- Not in RequireAuth itself (mixes auth-guard concerns with WS
  lifecycle).
- Not in AppShell (single-responsibility; the host stays trivially
  greppable).
- Not in `<Dashboard>` (would tear down on nav to Settings — losing
  the badge).
- **Survives navigation between `/` and `/settings/*`.** The user
  expects the run-state badge to keep working in Settings.

Implementation: `useEffect(() => { statusStream.connect(); return () => statusStream.disconnect(); }, [])`. The Phase 7
`StatusStream.connect()` is idempotent (`if (this.ws !== null) return;`);
StrictMode double-mount in dev is benign (verified). Logout →
RequireAuth unmounts → cleanup runs → socket closes.

Server-side cookie revocation: WS closes 4401 → StatusStream sets
`wantOpen = false` and emits `unauthorized` (no reconnect storm); the
global bus redirects to `/login`; RequireAuth unmounts the host.

## S. Polling fallback

**Lock:** none. WS reconnect machine + `state.full` snapshot deliver
deterministic resync; the `ReconnectingBanner` is the user-visible
truth-channel. Adding REST polling would create a double source of
truth and race the WS reconciliation. STOP stays interactive while
disconnected (HTTP escape hatch).

## T. Folder layout

```
web/src/
├── pages/
│   └── Dashboard.tsx
├── hooks/
│   ├── useRunState.ts
│   ├── useTargets.ts
│   ├── useStatusStream.ts            # cache patcher + useWsConnectionStatus
│   └── useRecentEvents.ts            # context consumer
├── components/
│   ├── RunStateBadge.tsx             # replaces Phase 7 stub in AppShell
│   ├── HeroCard.tsx
│   ├── TargetTile.tsx
│   ├── TargetDetails.tsx             # slide-out
│   ├── Sparkline.tsx
│   ├── LogViewer.tsx
│   ├── MetricGrid.tsx
│   ├── InlinePromptCard.tsx
│   ├── RecentEventsMenu.tsx
│   ├── ReconnectingBanner.tsx
│   ├── StatusStreamHost.tsx
│   ├── ErrorBoundary.tsx
│   └── RecentEventsProvider.tsx      # Context + reducer
├── lib/
│   ├── wsReducer.ts                  # pure applyWsEventToCache(prev, event)
│   ├── wsReducer.test.ts             # Vitest, pure unit tests
│   └── schemas/                      # see §E
└── (existing Phase 7 files unchanged)
```

## U. Bundle budget projection

Phase 7 baseline: 139 KB gzipped. Phase 8 projection: ~170 KB. Budget
ceiling: 250 KB. ~30% headroom — no lazy splitting required for
Phase 8.

If the budget bites later: first lazy-split candidate is the
`TargetDetails` body (Sparkline + LogViewer + MetricGrid load on first
slide-out open). Second: `RecentEventsMenu` body.

## V. Reduced-motion checklist for Phase 8

The global tokens.css `prefers-reduced-motion` block clamps every
duration to 16ms and iteration-count to 1. Per-component overrides
needed:

| Element | Override needed |
|---|---|
| Slide-out translateX | YES — switch to opacity-only via `motion-reduce:translate-x-0 motion-reduce:transition-opacity` |
| RecentEventsMenu open (scale+opacity) | YES — opacity-only via `motion-reduce:scale-100 motion-reduce:transition-opacity` |
| Spinners (`CircleNotch`, `ArrowsClockwise`) on `STARTING` / `ERRORED` tiles | YES — KEEP spinning; spinners are liveness signals (WCAG 2.3.3 exception). Add `@media (prefers-reduced-motion) { .animate-spin { animation-duration: 1s !important; animation-iteration-count: infinite !important; } }` to tokens.css. |
| Live badge dot pulse | No override — iteration-count clamp halts the pulse after one cycle, acceptable. |
| Hero button spring + variant flip + tile hover lift | No override — 16ms clamp lands these as effectively instant, acceptable. |
| Banner entrance | No animation; nothing to clamp. |
| Sparkline | Reads `useReducedMotion()` and switches to straight lineTo segments (no bezier). |

## W. Critical "must not get wrong" list

1. **Single `aria-live="polite"` for run-state.** `RunStateBadge` replaces the
   stub *body* in place; the wrapping live region stays. Do NOT add a
   second live region for run-state announcements (the page-title
   announcer is a distinct concern, design-system §9-compliant).
2. **`useStatusStream` is the sole writer to `["run","state"]`** outside its own `queryFn`. Mutations never `setQueryData` for run-state.
3. **Always Zod-validate WS envelopes' `data`** via `WsKnownEvent.safeParse`.
   Drop on failure (forward-compat); never trust the wire shape.
4. **4401 close MUST NOT loop-reconnect.** Phase 7 contract — verify it's still respected (it is in `ws.ts:144`).
5. **Bundle stays under 250 KB gzipped.** No new deps for problems 30 lines of
   in-house code solve (ErrorBoundary, no `react-error-boundary`).
6. **WS deltas tolerate "seed not present"** via `prev => prev ? patch(prev) : prev`.
7. **Slide-out URL state validates** against the targets cache. Unknown id silently closes.
8. **Mutations do NOT opt out of `globalApiErrorBus`** for 401/503. Auto-redirect is the desired UX.
9. **`runStartedAt` never ticks from zero on reconnect.** With §A's
   backend delta, `state.full.run_state_changed_at` is authoritative
   after reconnect.
10. **`<StatusStreamHost>` connects exactly once per authenticated session.**
   Mounted as a sibling of `<AppShell>` inside `<RequireAuth>`; survives
   nav between routes; tears down on logout.
11. **Phosphor icons everywhere.** No unicode glyphs ●/◐/✕/⚠ in tile
   status (design-system §6.3 mockup notation only; F# UI-E.theme-svg
   extends here).
12. **Hero `offline` ingest URL** derived from `window.location.host`;
   plaintext key reveal deferred to Phase 9 with a "Reveal in Settings"
   link in the meantime.

## X. Acceptance criteria (Phase 8 done)

- Backend `RunStateView.run_state_changed_at` shipped + tested + ws/REST exposes it.
- `npm run typecheck` clean.
- `npm run lint` clean (zero warnings).
- `npm run test` clean — Vitest pure unit tests for `applyWsEventToCache`.
- `npm run build` succeeds; `check-bundle-size.mjs` reports total ≤ 250 KB gzipped (target ~170 KB).
- Dashboard renders correctly in every RunState × every TargetUiState combination (use a manual dev fixture page if helpful).
- Sparkline draws 300 1-Hz samples at 60 fps without dropping (visual check).
- Slide-out: focus moves to close button on open, returns to activator on close (manual a11y check + browser back closes it).
- WS reconnect tested by force-killing the dev backend → ReconnectingBanner appears, tiles gray, reconnect succeeds with `state.full` replacing the cache; banner disappears.
- VK paste mid-flow works: PUT credentials, invalidate `["targets"]`, POST start, supervisor spawns workers for the now-credentialled targets.
- Confirm-on-stop morph: keyboard nav between pills, Escape == Cancel, 3s auto-revert, focus returns sensibly.
- All user-facing strings flow through `messages.ts::t()`; CI lint (jsx-no-literals) passes.

## Y. Doc-pass follow-ups (logged, NOT blocking Phase 8)

1. **ux-flows §2 VK step 5** ("keys are sent in the start payload") is wrong — update to PUT-credential-then-POST-start.
2. **design-system §1** raw HSL components vs Phase 7 source-form (full `hsl(...)` values) — update §1 to reflect what tokens.css ships.
3. **design-system §6.3** unicode status icons ●/◐/✕/⚠ are mockup notation — update to Phosphor-only commitment.
4. **design-system §6.9** "Reads `--color-accent` from runtime token export" wrapping note conflicts with Phase 7 source form — update to "read resolved value verbatim".
5. **design-system §2** `--text-display` token description "Run timer 00:17:42" implies 48px in the badge — update to "reserved for future presenter view".
6. **Backend (Phase 9 backlog):** add an ingest-reveal endpoint (`POST /api/settings/reveal-ingest-key` reprompt-protected, returns plaintext once); enables Phase 9 hero card stream-key reveal.
7. **Backend (Phase 9 backlog):** `SettingsUpdateRequest.first_run_complete` or `POST /api/settings/mark-first-run-complete` so the client can drop the localStorage flag.
8. **Backend (Phase 9+ backlog):** surface ingest-side metrics (host/port and ingest-worker bitrate/fps/drops) — Phase 8 falls back to aggregate target metrics in the hero LIVE body.
