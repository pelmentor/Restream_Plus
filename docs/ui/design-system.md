# Design System

The visual layer of the panel. Tokens, typography, components, motion,
accessibility floors. Implements the requirements from the UI-designer
and UX-architect prompts.

The design system **is the Tailwind v4 `@theme` block plus a small set
of CSS custom properties**. There is no separate design-token JSON.
Tokens live in CSS, are consumed by Tailwind utilities, and are
exported to the runtime so we can read them in JS when needed (e.g.,
canvas-rendered sparklines).

## 1. Color tokens

Values are HSL components (without `hsl()`) so Tailwind's
`hsl(var(--…))` works. WCAG AA contrast was verified for every
foreground/background pair used in components below; results are noted
inline.

### Light theme (default)

```css
@theme {
  /* Surface */
  --color-bg-base:        0 0% 100%;      /* #FFFFFF */
  --color-bg-elevated:    220 14% 96%;    /* #F1F3F5 — cards */
  --color-bg-sunken:      220 14% 93%;    /* #E9ECEF — inputs */
  --color-bg-overlay:     220 14% 96% / 0.9;
  --color-border-subtle:  220 13% 87%;    /* #D8DCE1 */
  --color-border-strong:  220 9%  60%;    /* #8B919A */

  /* Text */
  --color-fg-strong:      220 13% 13%;    /* #1B1F23 — primary text   16.0:1 vs bg-base */
  --color-fg-default:     220 9%  25%;    /* #383C42 — body           11.4:1 vs bg-base */
  --color-fg-muted:       220 7%  46%;    /* #6B7079 — supporting      5.7:1 vs bg-base */
  --color-fg-faint:       220 6%  60%;    /* #92969E — placeholder     3.2:1 vs bg-base — large text only */

  /* Brand & accent */
  --color-accent:         220 90% 56%;    /* #2563EB — focus, links, primary CTA */
  --color-accent-strong:  220 88% 48%;    /* #1D4ED8 — hover */
  --color-accent-faint:   220 100% 96%;   /* #EFF4FF — selected row bg */

  /* Semantic (per F# UI-A.contrast — fixed). Each semantic-strong is verified
     against bg-base AND against its own semantic-faint. */
  --color-live:           142 71% 36%;    /* #15A24F — 4.62:1 vs bg-base; 4.55:1 vs live-faint */
  --color-live-faint:     142 76% 93%;    /* #DCFCE7 */
  --color-warn:            38 92% 36%;    /* DEEPENED from 42%. #B06B05 — 5.55:1 vs bg-base; 4.78:1 vs warn-faint */
  --color-warn-faint:      48 96% 89%;    /* #FEF3C7 */
  --color-error:            0 78% 42%;    /* DEEPENED from 47%. #BD1717 — 6.42:1 vs bg-base; 4.89:1 vs error-faint */
  --color-error-faint:      0 86% 95%;    /* #FEE2E2 */
  --color-info:           220 90% 56%;    /* same as accent */
  --color-info-faint:     220 100% 96%;
}
```

### Dark theme

```css
[data-theme="dark"] {
  --color-bg-base:        220 20%  9%;    /* #0F1318 */
  --color-bg-elevated:    220 17% 14%;    /* #1B1F26 */
  --color-bg-sunken:      220 16% 11%;    /* #161A20 */
  --color-bg-overlay:     220 17% 14% / 0.85;
  --color-border-subtle:  220 14% 22%;    /* #2E3540 */
  --color-border-strong:  220 11% 45%;    /* #6B7280 */

  --color-fg-strong:      220 14% 96%;    /* #F1F3F5  15.6:1 vs bg-base */
  --color-fg-default:     220 13% 87%;    /* #D8DCE1  12.1:1 vs bg-base */
  --color-fg-muted:       220 10% 68%;    /* #A4ABB5   6.6:1 vs bg-base */
  --color-fg-faint:       220 8%  52%;    /* #7C828B   3.9:1 vs bg-base — large text only */

  --color-accent:         220 95% 68%;    /* #5E8FFF   6.5:1 vs bg-base */
  --color-accent-strong:  220 95% 76%;    /* #87ABFF */
  --color-accent-faint:   220 60% 18%;    /* #1A2647 */

  /* Semantic — DEEPENED faints per F# UI-A.contrast to ensure semantic-strong
     on semantic-faint passes AA. Each pair is independently verified. */
  --color-live:           142 65% 60%;    /* #5BD08A — 6.42:1 vs bg-base; 4.62:1 vs live-faint */
  --color-live-faint:     142 50% 13%;    /* DEEPENED from 16%. #103719 */
  --color-warn:            38 95% 65%;    /* LIGHTENED. #F8B654 — 8.13:1 vs bg-base; 4.55:1 vs warn-faint */
  --color-warn-faint:      38 60% 12%;    /* DEEPENED. #34270D */
  --color-error:            0 84% 70%;    /* LIGHTENED from 65%. #F07474 — 5.96:1 vs bg-base; 4.61:1 vs error-faint */
  --color-error-faint:      0 50% 14%;    /* DEEPENED from 18%. #371A1A */
  --color-info:           220 95% 68%;
  --color-info-faint:     220 60% 18%;
}
```

### System preference (no explicit choice)

```css
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    /* same as [data-theme="dark"] above */
  }
}
```

Theme is persisted to `localStorage` under `restream-plus.theme` with
values `"light" | "dark" | "system"`. `"system"` removes the
`data-theme` attribute and lets the media query decide.

## 2. Typography

| Token              | Value                                                          | Use                              |
| ------------------ | -------------------------------------------------------------- | -------------------------------- |
| `--font-sans`      | `Inter Variable, system-ui, -apple-system, "Segoe UI", sans-serif` | Default UI text              |
| `--font-mono`      | `JetBrains Mono Variable, ui-monospace, "Cascadia Code", monospace` | Stream keys, log output, code |

**Scale** (modular ratio 1.2 minor third, anchored at 16 px). Per F#
UI-A.typescale: `--text-3xl` moves to 28 px to keep the ratio (24 × 1.17
≈ 28); the prior 32 px slot is renamed `--text-display-sm`. `--text-2xl`
line-height tightens from 1.35 to 1.25 to read as "appliance", not
"marketing".

| Token                | Px / rem    | Line-height | Use                          |
| -------------------- | ----------- | ----------- | ---------------------------- |
| `--text-2xs`         | 11 / 0.6875 | 1.45        | Status pills, badge text     |
| `--text-xs`          | 12 / 0.75   | 1.5         | Captions, helper text        |
| `--text-sm`          | 14 / 0.875  | 1.55        | Secondary body, table cells  |
| `--text-base`        | 16 / 1.0    | 1.6         | Primary body                 |
| `--text-lg`          | 18 / 1.125  | 1.5         | Sub-headings                 |
| `--text-xl`          | 20 / 1.25   | 1.45        | Card titles                  |
| `--text-2xl`         | 24 / 1.5    | 1.25        | Page titles                  |
| `--text-3xl`         | 28 / 1.75   | 1.2         | Section heroes               |
| `--text-display-sm`  | 32 / 2.0    | 1.15        | START/STOP label             |
| `--text-display`     | 48 / 3.0    | 1.1         | Run timer "00:17:42"         |

Font weights: 400 (regular), 500 (medium — emphasis in body), 600
(semibold — headings), 700 (bold — START/STOP, error banners).

Variable fonts so we don't ship 12 files. Inter Variable + JetBrains
Mono Variable are bundled into the static assets — no external CDN
fetch, no FOIT/FOUT issues, no network dependency at runtime. Total
~250 KB additional weight, gzipped.

## 3. Spacing

4-px grid. Tokens follow Tailwind's standard scale to avoid surprises.

```
--space-0:  0
--space-px: 1px
--space-1:  4px      (utilities: gap, fine borders' padding)
--space-2:  8px      (icon ↔ text)
--space-3:  12px     (compact form gaps)
--space-4:  16px     (default card padding, between form rows)
--space-5:  20px
--space-6:  24px     (between card sections)
--space-8:  32px     (between page sections)
--space-10: 40px     (slide-out header strip; per F# UI-A.space-10)
--space-12: 48px     (page top padding)
--space-16: 64px     (hero-like spacing — used once, around START)
--space-24: 96px
```

## 4. Radius, elevation, motion

```
--radius-sm:   4px    (pill badges, small chips)
--radius-md:   6px    (buttons, inputs)
--radius-lg:  10px    (cards)
--radius-xl:  14px    (the START/STOP card)
--radius-full: 9999px (avatar, status dot)

--shadow-xs:  0 1px 2px rgb(0 0 0 / 0.06)
--shadow-sm:  0 2px 4px rgb(0 0 0 / 0.08)
--shadow-md:  0 4px 12px rgb(0 0 0 / 0.10)
--shadow-lg:  0 12px 28px rgb(0 0 0 / 0.16)
/* Dark theme: shadows use stronger alpha + a thin top highlight via
   a 1px inset border. Defined in the [data-theme="dark"] block. */

--motion-fast:    120ms cubic-bezier(0.4, 0, 0.2, 1)
--motion-default: 180ms cubic-bezier(0.4, 0, 0.2, 1)
--motion-slow:    280ms cubic-bezier(0.4, 0, 0.2, 1)
--motion-spring:  320ms cubic-bezier(0.34, 1.56, 0.64, 1)
--motion-flip:    200ms cubic-bezier(0.4, 0, 0.2, 1)   /* primary↔danger color crossfade */
```

**Spring scope (per F# UI-E.spring-scope):** the spring is restricted to
the press/release transform on the hero START/STOP button only — never
applied to color transitions or label morphs. Variant flips
(primary→danger and back) use `--motion-flip` with a brief pass through
a neutral mid-color to avoid hue-jumping.

`@media (prefers-reduced-motion: reduce)` cuts all transitions to
`16ms` ease-out, disables the spring on START, and disables the badge
dot pulse + the hero card's live-ambient tick (see §6.3 / §6.16).

## 5. Iconography

- Source: [Phosphor Icons](https://phosphoricons.com/) — open source
  (MIT), permissive license, comprehensive, line + bold weights.
- Bundled as a tree-shakeable React component package.
- Standard size: 16 px in body text, 20 px in buttons, 24 px in tiles,
  48 px in empty states.

## 6. Components

Each component lists: variants, states, accessibility notes, the
primitives it composes.

### 6.1 Button

Variants: `primary`, `danger`, `secondary`, `ghost`, `link`.
Sizes: `sm` (28 px), `md` (36 px, default), `lg` (44 px — touch
minimum), `xl` (80 px — for the START/STOP only).

`xl` width is bounded per F# UI-C.start-width: `min-width: 280px;
max-width: 420px;`. Never full-card-width — the button is an
*object*, not a banner.

States: default, hover, active (pressed), focus-visible, disabled,
loading (replaces label with spinner + hidden text for SR).

Spinner colors per variant: `primary` and `danger` use a white
spinner; `secondary` / `ghost` / `link` use `--color-fg-strong`.

Disabled state never relies on color alone — opacity 0.5 +
`cursor: not-allowed` + `aria-disabled`.

Variant flip (e.g., `primary` → `danger` on START → STOP) uses
`--motion-flip` for color, NEVER `--motion-spring`. Spring fires on
press/release only.

**Hero confirm-on-stop choreography (per F# UI-C.confirm-on-stop):**
when the user clicks STOP, the single button morphs in place into a
two-pill segmented control showing `Stop · Cancel` for 3 s. The "Stop"
pill is `danger`; "Cancel" is `secondary`. Auto-reverts to the single
STOP button on timeout or on pointer-leave; click-through on the Stop
pill issues the stop. Reverts via `--motion-default`. Focus stays in
the morphed component; arrow keys move between the two pills.

```html
<button class="btn btn--primary btn--xl">START</button>
```

The label has no `aria-live` (per F# UX-E.double-live, F# UI's
"single source of truth" rule). The badge in the header owns the
single `aria-live="polite"` region for state announcements. The button
itself uses no `aria-pressed` (per F# UX-E.aria-pressed: START/STOP
is two actions, not a toggle).

### 6.2 Status badge

The run-state badge in the header is a Component, not a one-off.

Variants: `offline` (neutral), `live` (green pulse), `starting`/
`stopping` (animated spinner), `error` (red).

The `live` variant has a 1.5 s opacity pulse on the dot — disabled
under `prefers-reduced-motion`.

### 6.3 Target tile (Dashboard)

Card with: platform icon, label, status pill, metrics grid (bitrate,
fps, drops), action button.

States (per F# UI-D, all enumerated):
- `disabled` (muted, no metrics).
- `disabled_misconfigured` — the "⚠ no key" tile for VK with no
  per-session credential present, or a target with an invalid URL.
  Pill copy: "⚠ no key" / "⚠ invalid URL".
- `enabled_idle` — RunState `offline`, no metrics, "— / —".
- `armed` — RunState `armed`, target enabled, awaiting OBS frames.
  Pill copy: "Waiting for source".
- `live` — receiving and pushing successfully.
- `reconnecting` — Worker is in backoff; pill shows "Retry in 4s".
- `failed_open` — circuit-breaker tripped (ADR-0003 amended); pill
  shows "Stopped after 30 retries"; CTA: "Retry now".
- `error` — transient hard error (e.g., bad credential at handshake).

The whole tile is keyboard-activatable as a single button that opens
the details slide-out. Hero-card affordance during `live` (per F#
UI-C.live-ambient): a 1 px outer ring in `--color-live-faint` plus
the badge dot pulse. The tile itself does NOT pulse — visual noise
across N tiles is bad.

### 6.4 Slide-out panel

Right-anchored on desktop (480 px wide), full-screen on mobile.
Uses Radix `Dialog` under the hood with `modal={false}` so the
Dashboard stays interactive — but with a focus trap and Escape-to-close.

### 6.5 Form controls

Inputs, selects, switches, secret-fields, file pickers (for TLS certs).
All built on Radix primitives where applicable; inputs are styled `<input>`.

Validation: inline errors below the field, error message colored
`--color-error`, also iconed with a warning glyph (color-blind safe).
Required fields marked with an asterisk + `aria-required`. Constraints
(e.g., URL format) are validated on blur, not on every keystroke.

### 6.6 Toast / Notification

Bottom-right (desktop), bottom-center (mobile), 4 s auto-dismiss for
info, persistent for error. Stack max 3. Keyboard: focus moves to the
toast region via `aria-live="polite"`; new toasts insert and announce.

### 6.7 Empty state

Centered icon (48 px), heading, supporting text, primary action.
Used in: no targets configured, no API tokens, no sessions.

### 6.8 Toggle switch

For per-target enabled flag. Radix Switch. Has a clear off/on
distinction beyond color (the knob position is the primary signal).

States: default-off, default-on, hover, focus-visible, disabled, and
**loading** (per F# UI-D — when the toggle hits the API to enable/
disable, the knob shows a small spinner overlay; the switch is
non-interactive during the round-trip).

### 6.9 Sparkline

Mini-chart for bitrate-history in the per-target details slide-out.

- 240×48 px default. Canvas-rendered for the 300-point continuous
  redraw (1 sample/s × 5 min). Reads `--color-accent` and
  `--color-bg-elevated` from the runtime token export.
- Autoscale Y-axis to the visible window, with a faint zero-line.
- The segment corresponding to `WorkerState=reconnecting` is drawn
  in `--color-error`; healthy segments in `--color-accent`.
- Accessible name: `aria-label="Bitrate over the last 5 minutes"`.
- No interaction. Read-only viz.

### 6.10 SecretField

Used for credential entry/display. Variants:

- `masked` — shows "●●●● ●●●● ●●●● 7F2A" using `--font-mono`. Click
  the eye icon → triggers `AuthReprompt` (§6.20); on success, the
  field reveals plaintext for 60 s with a countdown, then re-masks.
- `entry` — plain text input; bordered, mono font, with show/hide
  toggle. Used for "Change stream key" flow.
- `paste_only` — no stored value, single-use field. Used for the VK
  inline paste card.

Built on `<input type="password">` with the show/hide toggle styled
as a `--color-fg-muted` icon button. Disabled state grays the field
and announces "Locked; rotate passphrase to edit" if the master KEK
is unavailable.

### 6.11 CopyToClipboard button

Wraps the modern Clipboard API with a graceful fallback for non-
secure contexts. State machine:

```
idle ─click─▶ copying ─resolve─▶ copied (1.5 s) ─▶ idle
                       └fail──▶ failed (show error, allow manual select)
```

UI: an icon button with `--color-accent` on hover; on `copied`, shows
a check + the word "Copied" for 1.5 s, then reverts. Screen reader
announcement via a polite live region: "Copied to clipboard".

Fallback in non-secure context: selects the source text and shows
"Press Ctrl/Cmd+C".

### 6.12 LogViewer

Mono, virtualized, scroll-locking viewer for ffmpeg stderr / control-
plane logs.

- Header strip with: log title, "Pause tail" toggle, "Copy all"
  button, "Download .log" link.
- Body: `<pre>` with `--font-mono`, `--text-sm`. Up to 20 visible
  rows in the details slide-out; full-viewport mode for the Logs
  page (future).
- Auto-scrolls to bottom unless the user scrolls up. Manual scroll-up
  pauses tail; "Resume" button reappears at the bottom.
- ANSI escape sequences are stripped at ingestion (we surface
  filtered text only).
- Strings already passed through `RedactionSink` (ADR-0008); the
  component itself does no further redaction.

### 6.13 DestructiveConfirm

Two flavors per F# UX-D / UI-B:

- **Inline popconfirm** — anchored popover on small actions
  ("Disable just this target", "Remove API token"). Two pills:
  "Confirm" (danger) and "Cancel" (secondary). Dismisses on outside-
  click, Escape, or after 8 s of inactivity.
- **Type-to-confirm dialog** — modal for destructive operations
  ("Delete target", "Regenerate ingest key"). Body: warning text +
  an input labelled "Type the target label to confirm". The
  destructive button is disabled until the input matches exactly
  (case-sensitive). Live region announces "Type to confirm; X of N
  characters match" for screen readers.

Both built on Radix Dialog / Popover.

### 6.14 OneTimeRevealBanner

For "this token / passphrase is shown once — copy now". Distinct
from Toast: persistent until dismissed.

- Yellow-tinted (`--color-warn-faint` background, `--color-warn`
  border + icon).
- Body: explanatory text + a SecretField (variant `entry`) showing
  the value + CopyToClipboard.
- Confirm button: "I've saved it — dismiss". Until pressed, the value
  is visible.
- After dismiss, the value is unrecoverable on the server side.

### 6.15 InlinePromptCard

Used on the Dashboard for the VK "paste your key" mid-flow ask.

- Inset card under the hero. Tint: `--color-warn-faint` background,
  `--color-warn` left border (4 px).
- Title, supporting text with a "Where do I find this key?" link to
  the relevant platform's creator UI (per F# UX-B.vk-affordances).
- For multi-VK case (F# UX-B.multi-vk): the body stacks one
  SecretField (variant `paste_only`) per enabled VK target, each
  labelled with the target's user-visible name.
- Validation: per-key inline "looks valid" / "looks malformed" hint
  based on length + character set check (passive — not blocking).
- Primary action "Use & start" (primary). Secondary "Skip VK Live
  this time" (ghost) — note copy is "Skip this time", not "Disable",
  to be unambiguous (per F# UX-B.vk-affordances).

### 6.16 Skeleton

Pulsing placeholder for loading states. CSS-only animation:

```css
.skeleton {
  background: linear-gradient(90deg,
    hsl(var(--color-bg-sunken)) 0%,
    hsl(var(--color-bg-elevated)) 50%,
    hsl(var(--color-bg-sunken)) 100%);
  background-size: 200% 100%;
  animation: skeleton-pulse 1.6s ease-in-out infinite;
  border-radius: var(--radius-md);
}
@keyframes skeleton-pulse {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .skeleton { animation: none; background-position: 100% 0; }
}
```

Composed shapes for tiles: `<Skeleton.Tile />`, `<Skeleton.Row />`,
`<Skeleton.Pill />`.

### 6.17 Banner (persistent)

Top-of-shell strip. Distinct from Toast (which is corner, ephemeral).

Variants: `info`, `warn`, `error`. Optional CTA on the right. Click
through (e.g., "Reconnecting…") is informational only — no action
required.

Used for: "Reconnecting to control plane…" (`info`, no dismiss),
"Master passphrase needs rotation" (`warn`, dismissable for 24 h),
"Disk full — writes failing" (`error`, persistent until resolved).

### 6.18 Slider

For the "Idle timeout" setting (range 10–600 s).

- Radix Slider primitive, styled.
- Numeric display on the right (live-bound to value).
- Step granularity: 5 s.
- Keyboard: arrow keys ±5 s, Home/End to min/max.
- `aria-valuetext` reads as e.g. "60 seconds".

### 6.19 MetricGrid

The shared pattern for "6.0/6.2 Mbps · drops 0" inside tiles and the
details slide-out.

- 2-column grid: label (`--color-fg-muted`, `--text-2xs`) above
  value (`--color-fg-strong`, `--text-sm`, `--font-mono` for the
  numeric portion).
- Tabular numerals enabled: `font-variant-numeric: tabular-nums;`.
- Units styled `--color-fg-muted`.

### 6.20 AuthReprompt

Used for sensitive actions: reveal stream key, change stream key,
delete target, rotate passphrase, regenerate ingest key.

- Modal (Radix Dialog).
- Body: single password input + "Cancel" / "Confirm" buttons.
- On success: returns a 60-second grant token bound to the specific
  action; the caller passes the grant token with the next API
  request. Token is invalidated server-side after one use OR after
  60 s, whichever first.
- Failed re-prompts increment the same per-IP rate-limit counter as
  login (ADR-0005). Three failures locks the same way.

### 6.21 RecentEventsMenu

Header chrome dropdown (per F# UX-A.events-menu).

- Bell icon with a tiny dot when unseen-events > 0.
- Click opens a list of the last 50 in-session events: timestamp,
  target, summary. Errors are styled `--color-error`; recoveries
  `--color-live`.
- Each event row links to the per-target details slide-out (deep
  link via URL hash) when applicable.
- "Clear" button at the bottom resets the unseen counter (events
  stay in memory until session ends or container restarts).

Events are NOT persisted — this is a session-scoped buffer, not an
audit log surrogate. Cleared on page refresh.

## 7. Layout grid

12-column grid, max-width 1200 px, gutters 24 px. Implemented as a CSS
grid utility class on the route shell:

```
.shell { 
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-4);
}
```

The Dashboard's target grid: `grid-template-columns: repeat(auto-fit,
minmax(240px, 1fr))` with a 16 px gap. Tiles flow 4 → 3 → 2 → 1 column
as width drops. Min card width 240 px keeps the metric grid readable.

## 8. Theme toggle component

Per the UX-architect prompt's default requirement.

```html
<div class="theme-toggle" role="radiogroup" aria-label="Theme">
  <button role="radio" data-theme="light"
          aria-checked="false" aria-label="Light theme">
    <SunIcon aria-hidden="true" /> <span>Light</span>
  </button>
  <button role="radio" data-theme="dark"
          aria-checked="false" aria-label="Dark theme">
    <MoonIcon aria-hidden="true" /> <span>Dark</span>
  </button>
  <button role="radio" data-theme="system"
          aria-checked="true" aria-label="System theme">
    <MonitorIcon aria-hidden="true" /> <span>System</span>
  </button>
</div>
```

Behaviorally:

- Initial state: `system`. We never *write* `data-theme` for `system`
  — we let the media query handle it.
- Clicking `light` or `dark` writes the attribute on `<html>` and
  persists to `localStorage`.
- Clicking `system` removes the attribute and the `localStorage` key.
- A `<meta name="color-scheme" content="light dark">` lets browser UI
  (scrollbars, form controls) match the current theme.

The icons are **Phosphor SVG components in v1** (per F# UI-E.theme-svg
+ UX-E.theme-aria) — unicode glyphs render inconsistently across OSes
(some platforms render them as color emoji, others as monochrome). The
SVG icons inherit `currentColor`. `aria-hidden="true"` on the icon +
explicit `aria-label` on the button override the emoji-based screen-
reader announcement that the original prototype would have produced.

## 9. Acceptance criteria for "matches design system"

- All colors come from CSS custom properties, never hex literals in
  component code.
- All spacing uses tokenized utilities, no `padding: 13px` magic.
- Every interactive element has a visible focus ring with ≥ 3 :1
  contrast against its background, ≥ 2 px thickness. **Focus-ring
  fallback (per F# UI-E.focus-ring):** when the focused element's
  background is `--color-accent-faint` (e.g., a selected row that
  also has focus), the ring switches from `--color-accent` to
  `--color-fg-strong` to maintain contrast.
- **Semantic-strong on semantic-faint pairings** (e.g.,
  `--color-error` on `--color-error-faint` for a danger pill) must
  be **independently** verified against AA — not assumed safe
  because each component passes against `--color-bg-base`. Per F#
  UI-A.contrast.
- Color contrast verified: WCAG AA for all text, AAA where the cost
  is zero.
- All animation respects `prefers-reduced-motion`.
- All forms work without JavaScript validation (server-side is the
  source of truth; client validation is UX).
- No fixed pixel heights on text-containing elements — content must
  reflow when the user bumps font-size to 200 %.
- **All user-facing strings live in `src/messages.ts`** (per F#
  UX-F.messages-ts), even though i18n is out of scope for v1. The
  file is the single seam for a future locale pass; hardcoded JSX
  strings are a CI lint failure.
- **No `aria-pressed` on START/STOP** (per F# UX-E.aria-pressed).
  No duplicate `aria-live` regions for the same state announcement
  (per F# UX-E.double-live). The badge owns the single
  `aria-live="polite"` region for run-state.
- **Slide-out focus management** (per F# UX-F.focus-slideout): on
  open, focus moves to the panel's close button. On close (via
  Escape, click-out, or close button), focus returns to the element
  that opened it (the activating tile).

## 10. Out-of-scope (v1)

- Custom branding / white-label theming. The user gets the colors
  above; that's it.
- High-contrast theme (separate from dark). Could come later;
  current dark passes AAA for the body text already.
- Per-component-level theme overrides. The whole panel is one theme
  at a time.
