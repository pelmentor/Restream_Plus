# Phase 7 — Frontend Foundation: Design Memo

Status: locked 2026-05-16, pre-coding.
Inputs: Backend Architect-style pass × 3 (UX, UI, Software) per Rule №3.
Scope: theme + shell + router + login + dashboard placeholder; the runnable SPA spine.

This memo records the decisions that govern Phase 7 implementation so that
Phases 8 (Dashboard + WS) and 9 (Settings) inherit a stable foundation
without re-litigating the patterns.

---

## A. Reconciled with backend reality (corrections to the brief)

- **`POST /api/auth/login` response does NOT carry `needs_password_change`.** The
  pre-coding brief speculated otherwise; Software Architect verified
  `app/api/schemas.py::LoginResponse` is `{user: UserSummary}` with no flag.
  No first-boot forced-rotation path is built in Phase 7. If product wants
  it, add `password_must_rotate` to `users` + `UserSummary` together; that
  is a future ADR.
- **Backend exposes no `GET /api/auth/me`.** Software Architect's #1 critical
  item: add it in Phase 7. The endpoint is the load-bearing primitive for the
  `<RequireAuth>` guard and lets the SPA know its own auth state on hard reload
  without a synthetic probe of an unrelated endpoint. Implementation is
  ~15 lines + one test. **In scope for Phase 7.**

## B. Routing & shell

**Locked answer.** React Router v7 data-router (`createBrowserRouter`).

```
/login   ─ AuthLayout (no header, no skip link) ─ <LoginPage/>
/unlock  ─ AuthLayout                            ─ <UnlockPage/>
/        ─ <RequireAuth> → ChromeLayout (header+main+footer)
            ├─ index    → <DashboardPlaceholder/>
            └─ /settings/* → React.lazy(() => import("./pages/SettingsPlaceholder"))
*        ─ <NotFound/>   (404 — minimal text-only)
```

- **Auth guard = `<RequireAuth>` wrapper, NOT a loader.** It reads
  `useQuery({queryKey: ["auth","session"], queryFn: () => apiFetch("auth/me", ...)})`.
  - `isPending` → `<AuthGate/>` (Skeleton card)
  - error with `code === "service_locked" | "service_unlocking"` → `<Navigate to="/unlock" state={{from}} replace/>`
  - error with `status === 401` → `<Navigate to="/login" state={{from}} replace/>`
  - data = user → render children
- **Locked is a separate route, not a render mode.** UX Architect's "render mode" idea
  was elegant but added a parallel state machine; routing to `/unlock` is one fewer
  branch in the AppShell. The locked screen still exposes the ThemeToggle so the
  user isn't blinded at 2 a.m.
- **`?next=` / `state.from` security:** only honor same-origin path values
  (`/^\/(?!\/)/`); anything else falls back to `/`. Open-redirect prevention even
  for single-tenant — habit matters.
- **Lazy:** Settings is lazy from day one (Phase 9 fills it). Login and
  Dashboard are eager (cold-boot screens; lazy adds a round-trip before paint).

## C. Theme bootstrapping

- **`index.html` inline bootstrapper stays as-is.** Reads `localStorage`,
  sets `data-theme` for `"light"|"dark"` only; absence = system mode. Catches
  errors silently (private mode). Add a one-line comment explaining the silence.
- **`ThemeManager`** owns:
  - localStorage read/write under key `restream-plus.theme`.
  - `matchMedia('(prefers-color-scheme: dark)')` listener so future canvas
    consumers (Phase 8 sparkline) re-read tokens. Cheap; no Phase 7 consumer,
    but the seam is correct now.
  - `storage` event listener so a theme change in tab A propagates to tab B.
- **`ThemeToggle`** is the 3-segment radio group per design-system §8:
  Phosphor `Sun`/`Moon`/`Monitor` icons at `weight="regular"`,
  `role="radiogroup"`, per-button `aria-checked` + `aria-label`. No sliding
  pill — the active segment is signalled by `data-state="checked"` styling
  (background swap). 32 px tall in a 64 px header.

## D. CSS architecture (Tailwind v4)

- **Single file `web/src/theme/tokens.css`**, imported FIRST in `main.tsx`.
  Cascade order: tokens → tailwind preflight → tailwind utilities → component CSS.
- **`@theme {}` block BEFORE `@import "tailwindcss"`** — this is v4's #1
  footgun; reverse order and tokens stop generating utilities.
- **Deviation from design-system §1 form (flagged):** tokens carry full color
  values (`--color-bg-base: hsl(0 0% 100%)`) not raw HSL components, so v4
  auto-generates `bg-bg-base`/`text-fg-strong`/etc. utilities for free. The
  *colors and contrast ratios are unchanged*; only the source form moves.
  `color-mix()` covers alpha (`bg-bg-base/80`). Design-system §1 will be
  updated in a follow-up doc pass to record this.
- **Dark-mode overrides live OUTSIDE `@theme`**, in `[data-theme="dark"]` +
  `@media (prefers-color-scheme: dark)` blocks. Inside `@theme` they'd
  re-register tokens and break the generator.
- **Component classing = `cn()` + raw utilities + small in-house `variants`/`sizes`
  maps.** No `class-variance-authority` (extra dep, learning surface, ~2KB
  unnecessary for 5×4 matrices). `clsx` is already in `package.json`.
- **Focus ring = ONE `@layer base` rule on `*:focus-visible`.** 2 px outline at
  `--color-accent`, 2 px offset. The accent-faint fallback (`[data-on-accent-faint]:focus-visible`)
  switches the outline to `--color-fg-strong` per design-system §9 / F# UI-E.focus-ring.
- **Fonts: DEFERRED out of Phase 7 (corrects design-system §2).** Self-hosted
  Inter Variable + JetBrains Mono Variable would be ~250 KB extra page weight.
  Phase 7 ships with the platform-font stack only (`system-ui, …`). The
  `--font-sans` / `--font-mono` tokens are still defined so callers don't care.
  Adding the woff2 files + `@font-face` declarations becomes a follow-up
  before Phase 9 lands the credential-display surfaces (where the monospace
  is most visible). Logged in §"Open follow-ups".

## E. API client + state

- **`lib/api.ts::apiFetch<T>(path, init, schema?)`** is pure:
  - Same-origin only (`credentials: "same-origin"`); base path `/api/`.
  - Throws a typed `ApiError {code, status, retryAfter?}` on non-2xx.
  - Validates with the optional Zod schema on success (when provided).
  - Has zero coupling to React Router, TanStack Query, or navigation.
- **`globalApiErrorBus`** (`mitt`-style 8-line emitter): the top-level handler
  in `App.tsx` subscribes; on `code === "unauthorized"` → invalidate session +
  `navigate("/login")`; on `service_locked` / `service_unlocking` →
  `navigate("/unlock")`. Login + Unlock pages themselves opt out (they
  expect 401 / 503 on the wire and handle inline).
- **TanStack Query v5 `QueryClient`** with these defaults:
  - `staleTime: 30_000`, `gcTime: 5*60_000`.
  - `refetchOnWindowFocus: false` (appliance UI; WS owns realtime).
  - `refetchOnReconnect: true`.
  - `queries.retry`: no for 4xx; once for 5xx; off for `ApiError` with
    `status in {401,403,422,429}`.
  - `mutations.retry: false` (user-initiated; never silently retry).
- **Session state** = `useQuery(["auth","session"], () => apiFetch("auth/me",…))`
  with `staleTime: Infinity`, `gcTime: Infinity`, `retry: false`. Mutations
  for login/logout call `queryClient.setQueryData(["auth","session"], …)`
  to update without a refetch round-trip. NO React Context for "who am I".
- **Query-key convention** (locks Phase 8/9 inheritance):
  - `["auth", "session"]`
  - `["targets"]`, `["targets", id]`, `["targets", id, "credential"]`
  - `["run", "state"]`
  - `["settings"]`, `["settings", "tokens"]`, `["settings", "sessions"]`
  - `["health", "readyz"]`
- **DevTools** only in dev via `import.meta.env.DEV && <ReactQueryDevtools/>`.

## F. WebSocket plumbing (Phase 7 stubs, Phase 8 consumes)

- **`lib/ws.ts` exports a module-singleton `statusStream`** class. Not a
  context; not a per-hook instance. Backed by a Set of listeners.
- Lifecycle: `connect()` / `disconnect()`. Subscribe via `subscribe(env => …)`
  returning unsubscribe; `onStatus(s => …)` for connection states.
- Reconnect backoff: 1 → 2 → 4 → 8 → 10s cap (per ux-flows §5).
- Close-code handling:
  - `4401` → auth_failed; emits `ApiError(unauthorized,401)` to globalApiErrorBus.
  - `4503` → locked; emits `ApiError(service_locked,503)`.
  - `1011` / `1006` / others → reconnect with backoff.
- Envelope: only `{v: 1, event, data}` shapes are dispatched; future v ignored.
- Phase 7 does NOT call `connect()` from anywhere — it just ships the class.
  Phase 8 will start the singleton from `<RequireAuth>`'s post-success effect.

## G. Login UX

- **Schema:** `z.object({ username: z.string().min(1), password: z.string().min(1) })`.
  Mirror the backend's permissive `LoginRequest`; do NOT enforce
  `password.min(10)` here — that gate belongs on change-password, not on a
  login form that might face an interim/old password.
- **Username:** hidden `<input>` with `autoComplete="username" value="admin" readonly>`
  in the form so password managers (1Password, Bitwarden, browser
  built-ins) correctly associate the saved credential. Not visible as a
  field (the appliance is single-admin).
- **Password input:** autofocus on mount. `autoComplete="current-password"`.
  Caps Lock detector (`event.getModifierState("CapsLock")` on keydown) shows a
  small `--color-warn` hint below the field. Cost ~15 lines; eliminates the
  most common "I typed it right!" failure.
- **Submit:** `useMutation` calling `apiFetch("auth/login", {method:"POST", json:values})`.
  Button has spinner + `disabled={isPending}`. On 200, `setQueryData(["auth","session"], data.user)`
  then `navigate(safeNext(location.state?.from) ?? "/", {replace:true})`.
- **`safeNext(s)`:** allow only paths matching `/^\/(?!\/)[\w\-./~%?&=#:]*$/`;
  anything else → null → fall back to `/`. Prevents open-redirect.
- **401 (`invalid_credentials`)**: `setError("password", {message: messages.login.invalidCredentials})`
  AND `setValue("password", "")`. Username stays.
- **429 (`rate_limited`)**: replace the entire form body with a `Banner` `warn`
  variant counting down via the parsed `Retry-After` header (integer seconds;
  fall back to 60s if unparseable). When it hits 0, the form remounts.

## H. Locked / Unlock screen

- Reached when any `/api/*` call returns 503 `service_locked` or
  `service_unlocking`. The globalApiErrorBus handler navigates to `/unlock`.
- Page renders the wordmark + ThemeToggle (the user must be able to read
  the screen) + a passphrase paste form + a `Banner`.
- `service_locked` (503): form active. POST `/api/unlock`. On 200, invalidate
  `["auth","session"]` (forces a fresh `/me` probe), navigate to `state.from ?? "/"`.
  Failure (`unlock_failed`) → inline error.
- `service_unlocking` (503 with Retry-After: 2): show progress card,
  refetch every 2s, transition to "ready" when next probe succeeds.

## I. Messages discipline

- `src/messages.ts` exports a typed `t()` helper + an `as const` messages
  object. Typo'd keys are TypeScript errors at compile time.
- ESLint enforces `react/jsx-no-literals` with `ignoreProps: true` and a small
  allow-list of typographic glyphs (`·`, `—`, `→`, `←`, ` `). Per-line
  escape hatches for brand strings ("Restream_Plus" in the wordmark) are
  the exception, not the norm.
- Every Phase 7 user-facing string (button labels, error copy, banner copy,
  the empty Dashboard subtitle) lives in `messages.ts` from day one.

## J. Components Phase 7 ships

Component | Notes
--- | ---
`Button` | Variants `primary | danger | secondary | ghost | link`; sizes `sm | md | lg | xl`. `xl` reserved for the hero START/STOP (Phase 8) with the design-system §6.1 width bounds.
`Banner` | `info | warn | error` variants; persistent header strip.
`Skeleton` | Tile / Row / Pill primitives.
`EmptyState` | Centered icon + heading + text + optional CTA (DashboardPlaceholder uses it).
`ThemeToggle` | 3-segment radio group; Phosphor SVG icons; `aria-radiogroup`.
`AppShell` | Header (wordmark + ThemeToggle + AccountMenu stub) + `<main id="main-content" tabIndex={-1}>` + footer (build SHA).
`AuthLayout` | No header; full-bleed centered card backdrop.
`RequireAuth` | The guard wrapper.

**Deferred to Phase 8 (NOT shipped now):**
- `RunStateBadge` — but the AppShell renders a static "OFFLINE" placeholder
  that owns the **single `aria-live="polite"` region** from day one so
  Phase 8 inherits the contract.
- `RecentEventsMenu` — entirely absent in Phase 7 (a bell that opens an
  empty popover is worse than no bell).
- Sparkline / LogViewer / Tile / Slide-out / InlinePromptCard — Phase 8.

## K. Focus + accessibility wiring

- **Skip link** as the first focusable element in `ChromeLayout`:
  visually hidden until focused, jumps to `<main id="main-content" tabIndex={-1}>`.
- **`useFocusOnRouteChange()`** in `ChromeLayout`: on pathname change,
  programmatically `mainRef.current?.focus()` AND announce the new page
  title via a SEPARATE `aria-live="polite"` region (the run-state badge
  owns one; the page-title announcer owns the second). Different concerns
  → not a violation of design-system §9.
- **`useReducedMotion()`** hook shipped now (one Phase 7 consumer:
  ThemeToggle segment transition). Phase 8 inherits it for the START
  spring + sparkline draw cadence.
- **AuthLayout has NO skip link** — the form is two tabs deep at most.

## L. Folder layout

```
web/src/
├── main.tsx                    # QueryClient + RouterProvider + DevTools
├── App.tsx                     # globalApiErrorBus subscriber + outer error boundary
├── router.tsx                  # createBrowserRouter
├── messages.ts                 # typed t() + as-const messages
├── theme/
│   ├── tokens.css              # @theme + dark + system + focus-ring layer
│   ├── ThemeManager.ts         # localStorage + matchMedia + storage event
│   └── ThemeToggle.tsx
├── lib/
│   ├── cn.ts                   # clsx wrapper
│   ├── api.ts                  # apiFetch + ApiError
│   ├── errors.ts               # ErrorCode string union
│   ├── eventBus.ts             # globalApiErrorBus
│   ├── queryClient.ts          # the configured QueryClient
│   ├── safeNext.ts             # open-redirect-safe next path
│   ├── useReducedMotion.ts
│   ├── ws.ts                   # StatusStream singleton
│   └── schemas/
│       ├── _shared.ts          # ApiErrorBody, datetime helper
│       ├── auth.ts             # UserSummary, LoginRequest/Response, UnlockRequest/Response
│       └── health.ts           # HealthResponse, CheckResult
├── components/
│   ├── Button.tsx
│   ├── Banner.tsx
│   ├── Skeleton.tsx
│   ├── EmptyState.tsx
│   ├── AppShell.tsx
│   ├── AuthLayout.tsx
│   └── RequireAuth.tsx
└── pages/
    ├── Login.tsx
    ├── Unlock.tsx
    ├── DashboardPlaceholder.tsx
    ├── SettingsPlaceholder.tsx  # lazy-loaded; "Settings coming in Phase 9"
    └── NotFound.tsx
```

- **No barrel files.** Direct import paths only (tree-shaking + import-cycle
  detection both benefit).
- **`@/` path alias = `src/`** (already configured in `tsconfig.app.json`).
- Component files use `PascalCase.tsx`; hooks and lib use `camelCase.ts`;
  CSS/JS scripts use `kebab-case`.

## M. Bundle-size guard

Phase 7 ships `web/scripts/check-bundle-size.mjs`: reads `dist/assets/*.{js,css}`,
gzips each, sums; fails with `process.exit(1)` if total > 250 KB. Wired into
`"build": "vite build && node scripts/check-bundle-size.mjs"`.

Local dev still gets `"vite"` (no check). Phase 11 CI inherits the guard for free.

## N. Open follow-ups (logged, not blocking)

- **Self-hosted Inter Variable + JetBrains Mono Variable.** Phase 7 ships
  with the platform-font stack; design-system §2 mentions woff2 self-hosting
  but the binaries aren't in the repo and Phase 7 has no monospace-heavy
  surface yet. Track to land before Phase 9's credential-display widgets.
- **`react/jsx-no-literals` allow-list refinement.** Start strict; tune
  the allow-list as the first few false positives surface during Phase 8.
- **Per-chunk bundle budgets.** Phase 7 enforces a single total budget.
  When Phase 9 splits Settings into a lazy chunk that grows, upgrade the
  guard to per-chunk thresholds.
- **`needs_password_change` flow.** Not built (backend has no field). If
  product wants it, file an ADR and add `users.password_must_rotate` +
  `UserSummary.password_must_rotate` together.
- **`BaseHTTPMiddleware` → pure ASGI middleware** (Phase 6 deferred). Convert
  when adding the next middleware. Phase 7 may add a `Cache-Control: no-store`
  on the HTML response — if so, do that conversion at the same time.

## O. Critical things the engineer MUST NOT get wrong

1. **`@theme` block BEFORE `@import "tailwindcss"`** in `tokens.css`. Reverse
   order and tokens stop generating utilities. The #1 v4 footgun.
2. **The hidden username `<input autocomplete="username" value="admin">`** in the
   Login form is load-bearing for password-manager compatibility. Without it,
   credential saving silently degrades.
3. **`__Host-rp_session` is HttpOnly.** JS cannot read it; do not write code
   that tries. Auth state is derived from API responses (200/401), never from
   `document.cookie`.
4. **Single `aria-live="polite"` region in the run-state badge from day one**
   (even when the badge is a static "OFFLINE" placeholder). Phase 8 must not
   be allowed to add a second; locking the contract early prevents the
   duplicate-announcement bug class.
5. **`safeNext()` validates `state.from` and `?next=` to same-origin path only**
   (`/^\/(?!\/)/`). Habit matters even on a single-tenant appliance.

## P. Acceptance criteria (Phase 7)

- `npm run typecheck` clean.
- `npm run lint` clean (including `react/jsx-no-literals` enforcement).
- `npm run build` produces `dist/` with total gzipped JS+CSS ≤ 250 KB. The
  postbuild script fails on exceedance.
- Theme toggle persists across reload; `data-theme` is applied before first
  paint (no FOUC on dark systems).
- Login → success → `/` works against the real backend.
- Locked-mode 503 → redirect to `/unlock` works against a locked backend.
- Backend `GET /api/auth/me` returns `UserSummary` for authenticated requests
  and 401 otherwise, with one test pinning the contract.
- All user-facing strings live in `messages.ts`; no hardcoded JSX literals
  beyond the documented allow-list / per-line escape hatches.
