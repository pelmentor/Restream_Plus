# UX Flows & Information Architecture

This document defines *how the panel feels* before any pixel is drawn.
Hierarchy, flows, and interaction patterns are decided here; visual
tokens (colors, spacing, typography) live in `design-system.md`.

The default-requirement from the UX-architect prompt — **light / dark
/ system theme toggle, always present** — is honored throughout. WCAG
2.1 AA is the floor, not the ceiling.

## 1. Top-level information architecture

The panel has exactly **two routes** (plus auth):

```
/login                         ─ first-boot password setup or normal login
/                              ─ Dashboard (default)
/settings                      ─ Settings (tabs for sub-sections)
  /settings/targets/twitch     ─ tab
  /settings/targets/youtube    ─ tab
  /settings/targets/kick       ─ tab
  /settings/targets/vk         ─ tab
  /settings/targets/custom     ─ tab list of user-added custom targets
  /settings/general            ─ ingest key, idle timeout, log retention
  /settings/security           ─ change password, rotate passphrase, API tokens
  /settings/sessions           ─ read-only past sessions history (per F# UX-A.session-history)
  /settings/about              ─ version, build SHA, license, log path, links
```

Why two routes and not five: there's nothing else for the user to *do*.
A streamer's panel exists to start the stream and to fix settings when
something's off. More routes = more places for the user to be lost in.

The persistent chrome (header) carries:

- App name (left, no logo wizardry — plain text wordmark).
- Run-state badge — single source of truth, also visible from any
  screen. Owns the **single `aria-live="polite"` region** for state
  announcements (per F# UX-E.double-live).
  - Click behavior (per F# UX-A.badge-from-settings): from `/` scrolls
    to the hero card. From any other route (e.g. `/settings/*`),
    navigates to `/`. The label always carries enough context for
    screen readers: "Live for 17 minutes", "Offline", "Error".
- **RecentEventsMenu** (bell icon with unseen-count dot) — opens a
  dropdown with the last 50 in-session events (per F# UX-A.events-menu
  + design-system.md §6.21).
- Theme toggle (light / dark / system, three-segment control with
  Phosphor SVG icons and per-button `aria-label`).
- Account menu (avatar circle with initial, dropdown: "Change
  password", "API tokens", "Build SHA: <short>", "Log out"). Build SHA
  is also shown in the page footer (per F# UX-A.version-surface).

The header is sticky, 64 px tall on desktop, 56 px on mobile. It does
not collapse on scroll — the run-state badge is too important to ever
hide.

## 2. Dashboard

The Dashboard is the start-of-day view. It optimizes for:

1. **Confidence at a glance**: is the stream live and are all my
   platforms healthy?
2. **One action**: START or STOP.
3. **Triage when something's off**: which target failed and what does
   ffmpeg say?

### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ ░ Restream_Plus                ● OFFLINE       ☀ light  🌙 dark  💻 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│        ┌──────────────────────────────────────────────────┐         │
│        │                                                  │         │
│        │                    [ START ]                     │         │
│        │                                                  │         │
│        │       Push OBS to: rtmp://<host>:1935/live      │         │
│        │           Key: ●●●●●●●● (click to reveal)        │         │
│        │                                                  │         │
│        └──────────────────────────────────────────────────┘         │
│                                                                     │
│  Targets:                                                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐    │
│  │ Twitch      │ │ YouTube     │ │ Kick        │ │ VK Live     │    │
│  │ ◐ enabled   │ │ ◐ enabled   │ │ ○ disabled  │ │ ⚠ no key    │    │
│  │ — / —       │ │ — / —       │ │ — / —       │ │ — / —       │    │
│  │             │ │             │ │             │ │             │    │
│  │ [config]    │ │ [config]    │ │ [config]    │ │ [config]    │    │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘    │
│                                                                     │
│  + Add custom target                                                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

When **live**:

```
┌─────────────────────────────────────────────────────────────────────┐
│ ░ Restream_Plus               ● LIVE 00:17:42   ☀ 🌙 💻              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│        ┌──────────────────────────────────────────────────┐         │
│        │                                                  │         │
│        │                    [ STOP ]                      │         │
│        │                                                  │         │
│        │   Ingest healthy · 6.2 Mbps · 60 fps · drops 0    │        │
│        └──────────────────────────────────────────────────┘         │
│                                                                     │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐    │
│  │ Twitch      │ │ YouTube     │ │ Kick        │ │ VK Live     │    │
│  │ ● live      │ │ ● live      │ │ ✕ error     │ │ ● live      │    │
│  │ 6.0/6.2 Mbps│ │ 6.1/6.2 Mbps│ │ reconnecting│ │ 2.5/2.5 Mbps│    │
│  │ drops 0     │ │ drops 0     │ │ retry in 4s │ │ drops 12    │    │
│  │ [details]   │ │ [details]   │ │ [details]   │ │ [details]   │    │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### START / STOP button states

Button states are derived from `RunState` (per ADR-0003 amended +
domain model in system-overview.md). The button is also **disabled**
when no Target is `enabled` (per F# UX-B.start-gating) — the empty-
state card replaces the hero in that case.

| Run state          | Button label    | Variant                              | Click action                                                                                                                                                  |
| ------------------ | --------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `offline`          | START           | primary                              | POST `/api/run/start`. If any enabled target is VK with no per-session credential present, the click triggers the **InlinePromptCard** (design-system.md §6.15) and defers the API call until the user submits keys. |
| `starting`         | STARTING…       | primary, disabled, spinner           | No action; transitions automatically to `armed` once Workers are spawned.                                                                                     |
| `armed` (NEW)      | STOP            | danger                               | Workers are running and waiting for OBS. The card shows a "Waiting for OBS to start streaming" banner (info variant). STOP issues `/api/run/stop`.            |
| `live`             | STOP            | danger                               | OBS is publishing. STOP issues a **confirm-on-stop** choreography (the button morphs to a `Stop · Cancel` two-pill segmented control for 3 s — see design-system.md §6.1). |
| `stopping`         | STOPPING…       | danger, disabled, spinner            | No action; transitions to `offline`.                                                                                                                          |
| `error`            | START           | primary with red outline             | Retry; the error toast underneath links to the per-target details slide-out of whichever Worker raised the error.                                            |
| no enabled targets | (button hidden) | empty-state card replaces hero       | "Add a target to get started" → links to `/settings/targets/twitch`.                                                                                          |

The single button never uses `aria-pressed` (per F# UX-E.aria-pressed
— START/STOP is two actions, not a toggle). Its label changes; the
badge's `aria-live` region announces transitions.

### VK Live "key required" flow

When the user clicks START and one or more enabled targets are VK Live
without a per-session credential present:

1. The START click does **not** issue the API call.
2. Inline below the START button: an **InlinePromptCard**
   (design-system.md §6.15) titled "Stream key required for VK Live".
3. The body **stacks one paste field per enabled VK target** (per F#
   UX-B.multi-vk), each labelled with the target's user-chosen name
   (e.g., "VK Live — main channel", "VK Live — community"). Each
   field has:
   - A passive validation hint underneath ("looks valid" /
     "looks malformed") based on length + character-set heuristic.
   - A "Where do I find this key?" link to the platform's creator UI
     (per F# UX-B.vk-affordances).
4. Buttons: **"Use & start"** (primary; disabled until all required
   fields are filled — validation hint is informational only, not
   blocking) and **"Skip VK Live this time"** (ghost; copy is
   deliberately "Skip this time", not "Disable", per F#
   UX-B.vk-affordances — the persistent enabled flag is not touched).
5. On submit, keys are sent in the start payload over HTTPS,
   immediately encrypted server-side, stored as Credentials with
   `lifetime=per_session` (ADR-0009), and the session begins.
6. After session ends (STOP or OBS disconnect + idle timeout), every
   per-session Credential is deleted by the supervisor (ADR-0008's
   END handler).

This flow is the one place in the app where we ask for input
mid-flight. Justified by the platform's actual behavior — see
`platforms-reference.md` §VK Live.

### Per-target tile interactions

- The whole tile is a button to expand details (in-page slide-out
  panel on the right, not a modal — so the Dashboard stays visible).
- **Slide-out focus management** (per F# UX-F.focus-slideout): on
  open, focus moves to the panel's close button. On close (via
  Escape, click-out, or close button), focus returns to the
  activating tile. The panel uses Radix Dialog with `modal={false}`.
- Details panel content (per F# UX-C.slideout):
  - **Bitrate sparkline** (last 5 minutes, design-system.md §6.9).
  - **MetricGrid** (§6.19): bitrate, fps, drops, target health.
  - **Error context**: timestamp of when the error started, retry
    count, last error short message.
  - **Log viewer** (§6.12): last 20 lines of ffmpeg stderr (already
    redacted via `RedactionSink`, ADR-0008). With **"Pause tail"**
    toggle, **"Copy all"**, **"Download .log"**.
  - **Actions**:
    - **"Retry now"** (when in `reconnecting` or `failed_open`):
      clears the backoff counter and pushes the Worker back through
      `starting`.
    - **"Disable just this target"** (per F# UX-C.slideout): inline
      popconfirm with explicit copy "Disable Kick? Other targets
      keep streaming." The destructive action is scope-explicit; no
      ambiguity with the global STOP.
    - **"Edit settings"**: deep-link to `/settings/targets/<type>`.

### Mobile (≤ 640 px wide)

- Header collapses to: wordmark, badge, theme toggle in an icon-button
  menu, account icon.
- START/STOP card is full width, button is 80 px tall (per the design
  prompt's 44 px touch-target floor — we go bigger because this is
  *the* primary action).
- Target tiles stack vertically. The details slide-out becomes a
  full-screen view with a back affordance.

## 3. Settings

Tabs along the left on desktop (sidebar 240 px), a horizontal tab
strip at top on mobile.

The sidebar header carries a **"← Dashboard"** link (per F#
UX-A.settings-breadcrumb) above the tabs — the wordmark click also
goes home, but the explicit breadcrumb makes the navigation obvious
to a user who deep-linked into a tab.

### General

- **Ingest key**: read-only display (revealable on click + password
  re-prompt), with a "Regenerate" button that confirms via dialog (this
  invalidates the OBS configuration the user has saved).
- **Server hostname / port**: read-only (informational; what to put in
  OBS).
- **Idle timeout** (seconds): the grace period workers wait after OBS
  disconnects before tearing down. Default 60 s. Slider 10–600.
- **Log retention** (days): per-target ffmpeg log retention. Default 14.
- **TLS**: status of the cert (self-signed / user-supplied / none),
  with a doc link.

### Targets (one tab per type)

For Twitch/YouTube/Kick:

- **Enabled** toggle (also reflected on Dashboard).
- **Label** (user-chosen, e.g., "Twitch — main channel").
- **Ingest URL**: dropdown (preset values per `platforms-reference.md`)
  or "Custom URL" with a free-text field.
- **Stream key**: secret field. Shows "●●●●●●●● 7F2A" when set;
  changing requires typing the new key + a password re-prompt.
- **Show key once** button: triggers a password re-prompt; on success
  reveals the key for 60 seconds with a copy-to-clipboard button.
- **Last status**: from the most recent session (timestamp + status).
- **Delete** (destructive, requires confirm with the target label
  typed to enable).

For VK:

- Same as above MINUS the persistent "Stream key" field. The field is
  replaced by:
  > "VK requires a fresh stream key for each broadcast. You'll paste it
  > when you start the stream. Keys are wiped after each session."
- A separate "Use a stored key for next session only" advanced toggle
  that lets the user paste a key here for one-shot reuse — for the case
  where they want to start headlessly via the API.

For Custom:

- List + "Add custom target" button → opens a form with the same fields
  as Twitch (label, URL, key, enable).

### Security

- **Change password**: old / new / confirm.
- **Rotate master passphrase**: old passphrase / new passphrase + a
  warning that all sessions and API tokens will be invalidated.
- **API tokens**: table (label, created, last used, revoke). "New
  token" opens an inline form, on creation shows the token once with
  copy-to-clipboard and a banner ("you will not see this token again").
- **Sessions**: list of active sessions with last-seen, revoke button,
  and "Log out everywhere".

### About

- Version, build SHA, image digest, link to GitHub repo, license
  (Apache-2.0).
- Server uptime, last 5 reboots' timestamps.
- **Logs path** (per F# UX-A.logs-path): the on-disk path to per-
  target ffmpeg logs and the application log — typically
  `/data/logs/`. Users with shell access on the host can `docker exec`
  + tail / download from there. v1 deliberately has no in-app full-
  log viewer beyond the slide-out's tail.
- Link to "Forgot password?" doc (per `ux-flows.md` §4) — the
  blunt-recovery path.

## 4. Auth flows

### First boot (no admin exists)

- Container started, `RESTREAM_ADMIN_PASSWORD` not set → random
  passphrase generated, printed once to stdout (also written to
  `/data/.first-boot-password` in 0400 mode — readable by the
  `restream` uid only).
- User opens `https://<host>/` → redirected to `/login`.
- Form shows: username (locked to `admin`), password.
- On success: redirect to Dashboard.

### Normal login

- User on `/login`, fills password, clicks "Sign in" → success →
  redirect to whichever route they were trying to reach (default `/`).
- After 5 failed attempts from one IP in 15 minutes: form disables for
  10 minutes with a countdown.

### Forgot password (deliberately blunt)

- Documented in `/settings/about` → "Forgot password? See docs."
- Linked doc explains: stop the container, restart with
  `RESTREAM_RESET_ADMIN_PASSWORD=1 RESTREAM_ADMIN_PASSWORD=<new>`,
  done. All sessions and API tokens are invalidated.

This deliberately matches the threat model: there is no email, no
recovery codes, no SMS. The user has shell on the host or they don't.

## 5. Real-time updates

The Dashboard subscribes to `/ws` immediately after auth. The server
broadcasts one message per second per active target (status + metrics),
plus state-change events (run state, target enabled/disabled, target
created/deleted, error events). All messages are JSON with a discriminator
`type` field.

On WS disconnect, the client retries with backoff (1, 2, 4, 8 s capped at
10 s), and shows a non-blocking banner "Reconnecting…". The Dashboard
remains usable from the last-known state during reconnect.

## 6. Empty / loading / error states

Per the UI-designer prompt, every container has all four states.

- **Empty (no enabled targets)**: the hero START/STOP card is
  REPLACED by an empty-state card: "Add a target to get started"
  with a primary button to `/settings/targets/twitch`. Disabled
  targets in the user's catalog are NOT counted — at least one
  target must be `enabled` for the hero to appear (per F#
  UX-B.start-gating).
- **First-run** (per F# UX-B.first-run): on the very first Dashboard
  load where no successful go-live has occurred, the hero card
  carries a 3-step numbered hint:
  1. Copy the ingest URL + key shown below into OBS.
  2. Start streaming in OBS.
  3. Click START here.
  Hint dismisses automatically after the first successful
  transition to `live` (persisted as a boolean in `settings`).
- **Loading (first paint, status unknown)**: tiles show a skeleton
  pulse (design-system.md §6.16), START is enabled if its other
  preconditions are met.
- **Error (API down)**: persistent **Banner** (§6.17 `info` variant)
  "Reconnecting to control plane…", tiles show last-known state
  grayed out, START disabled with tooltip "Reconnecting".
- **Empty target list filtered**: e.g., "No custom targets yet" with
  a button to add one.

## 7. Accessibility notes

- All interactive elements have visible focus rings (via Tailwind's
  `focus-visible` modifier). Two-pixel offset outline at
  `--color-accent`. **Focus-ring fallback** for accent-faint
  backgrounds — see design-system.md §9.
- The START/STOP button is a native `<button>`. **No `aria-pressed`**
  (per F# UX-E.aria-pressed): START/STOP is two actions on two
  states, not a toggle. State transitions are announced via the
  **single `aria-live="polite"` region in the run-state badge** (per
  F# UX-E.double-live).
- Per-target tile expansion is a `<button>` that toggles
  `aria-expanded` on the slide-out panel. **Slide-out focus
  management** (per F# UX-F.focus-slideout): on open, focus moves to
  the panel close button; on close, focus returns to the activating
  tile.
- Theme toggle is a `role="radiogroup"` with three radios. Each
  radio has an explicit `aria-label` (e.g., `aria-label="Light
  theme"`) so the screen-reader announcement does not depend on
  emoji-vs-glyph semantics. Icons are Phosphor SVGs with
  `aria-hidden="true"` (per F# UX-E.theme-aria).
- Color is never the only signal. The status icons (●, ◐, ✕, ⚠) carry
  semantic info alongside color.
- Keyboard: Tab order goes header → START/STOP → first target → … →
  Add custom. Escape closes the details slide-out and returns focus
  per the rule above.
- **Sensitive-action authentication re-prompt** uses the
  `AuthReprompt` component (design-system.md §6.20). On success it
  returns a 60-second grant token bound to the specific action, used
  exactly once by the next API call.

## 8. Out-of-scope (v1)

- No light/dark sync to the platform theme (we let the user pick).
- No multi-language. English only in v1. **All user-facing strings
  live in `src/messages.ts`** from day one (per F# UX-F.messages-ts) —
  i18n itself is deferred, but the seam exists so a future locale
  pass is not a refactor.
- No "schedule a stream start" feature.
- No notifications (email, webhook) on target errors. The
  `RecentEventsMenu`, the Dashboard tiles, and `/data/logs/` are the
  only feedback channels.
- No full log viewer page (per F# UX-A.logs-path). The
  `/settings/about` page documents the path `/data/logs/<...>.log`;
  users with shell access view the full retention there.

## 9. Settings → Sessions (history)

Read-only list of past sessions (per F# UX-A.session-history). Source:
the `sessions_history` table (ADR-0007). Columns:

- Started at (relative + tooltip absolute).
- Duration.
- Targets active at start (chip list).
- Outcome per target: live / dropped (with count) / failed_open /
  disabled-mid-session.
- End reason: user-stopped / OBS-disconnected (idle timeout) /
  control-plane-crash-recovered.

Pagination: 50 rows per page, newest first. No editing. No deletion
from the UI (sessions are part of the audit trail; deletion would
need a separate explicit operator flow that is out of scope for v1).
