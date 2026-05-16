# ADR-0002: Frontend stack — React 19 + Vite + TypeScript + Tailwind v4

## Status
Accepted — 2026-05-15

## Context

The web panel is small in surface area: a Dashboard (one big START/STOP, live
status tiles per target, server status) and a Settings area with one section
per target plus general server settings. There's no marketing site, no
multi-page navigation beyond the panel's own routes, no SEO concern (it
runs behind the user's own Docker host).

What matters for the choice:

1. **Component-level state + real-time updates from a WebSocket.** Live
   bitrate/fps tiles must update at 1 Hz without re-rendering the world.
2. **Design tokens and theming.** UI/UX prompts require a light/dark/system
   theme toggle as a default, WCAG AA contrast, and a clean design-token
   surface. The chosen stack must let us define tokens once and consume
   them in components without fighting the framework.
3. **DX for the only developer (me + the user).** Hot reload, type safety,
   and a component library or primitives that don't need months of design
   work to look professional.
4. **Bundle size + complexity.** Has to fit in the static assets served by
   the FastAPI app. No SSR. No server runtime on the frontend.

## Decision

**React 19 + Vite 6 + TypeScript 5.x + Tailwind v4 + Radix UI primitives**,
served as a static SPA out of `/web/dist/` by the FastAPI control plane.

Specific deps:
- `react`, `react-dom` (v19, RSC not used — this is a pure SPA).
- `vite` (build + dev server).
- `typescript` (strict mode).
- `tailwindcss` v4 (CSS-first config via `@theme {}`, native CSS variables
  — design tokens become first-class CSS without a build-time codegen step).
- `@radix-ui/react-*` for accessible primitives (Dialog, Switch, Tabs,
  Toast, Tooltip). We compose our own styled components on top — no shadcn
  copy-paste sprawl, just direct use.
- `react-router-dom` (client-side routing for Dashboard ↔ Settings).
- `@tanstack/react-query` (HTTP cache, retries, optimistic updates for the
  Settings forms).
- `zod` (form validation, runtime type-checks at the API boundary —
  matched 1:1 with the Pydantic v2 models on the backend, but we own
  separate definitions because RULE №2 forbids generated cross-language
  shims that we'd be locked into).
- `react-hook-form` (form state for Settings).
- No state management library. React's built-ins + react-query cover the
  entire need.

## Consequences

**Easier:**
- Tailwind v4's `@theme` block lets us declare design tokens as CSS custom
  properties at the source. The UX-architect prompt mandates a CSS
  variable-driven token system; Tailwind v4 *is* that, and gives us the
  utility ergonomics on top for free.
- Radix primitives ship accessible interaction logic (focus-trap in dialogs,
  arrow-key navigation in tabs, ARIA states wired up). We do not write
  these from scratch and we don't ship a heavyweight component library.
- React Query owns the "is this status fresh / when do I refetch" problem
  so our components stay declarative.
- Bundle stays small: Tailwind v4 + tree-shaken Radix + the SPA itself
  comfortably fits under 250 KB gzipped for the whole panel. Verified by
  build-time budgets in CI (see ops/deployment.md).

**Harder:**
- React 19 is recent; some libraries lag. Mitigated by pinning to known-
  compatible versions in `package-lock.json` and by the small dep surface.
- Tailwind v4 changed `tailwind.config.js` → CSS-first `@theme`. If we ever
  need a config-driven plugin ecosystem, we'd feel it. We don't.
- Two type systems on the wire (Pydantic on backend, Zod on frontend). We
  accept the duplication as an intentional decoupling — see RULE №2.

**Rejected alternatives:**
- **SvelteKit 2**: smaller bundle, less boilerplate. Rejected because the
  Radix-equivalent ecosystem on Svelte (`bits-ui`, `melt-ui`) is less
  mature for the specific primitives we need (sliding panels, complex
  form widgets), and React's recruitability matters slightly even for a
  single-dev project (future contributions, debugging via Stack Overflow
  surface).
- **Plain HTML + Lit web components**: smallest possible bundle, zero
  framework runtime. Rejected because writing the Settings forms by hand
  with vanilla DOM APIs is many days of work that React + react-hook-form
  + Radix gives us in a few hours. The build artifact size delta does not
  justify it for a self-hosted appliance.
- **Solid.js**: technically excellent. Rejected for ecosystem reasons —
  every other dep we want (Radix, react-query, react-hook-form) has a
  Solid port but they are not 1:1, and matching them is a project of
  its own.
- **No build / vanilla ESM modules**: would require shipping a CDN-style
  import map, and Tailwind v4 still needs a build step for the CSS. Not
  worth the asymmetry.

## Open questions / revisit triggers

- If React 19 + a critical dep proves incompatible during build-out, we
  pin to React 18.3 — same surface API, no rewrite needed.
- If bundle size grows beyond 300 KB gzipped, evaluate code-splitting
  Settings into its own chunk lazy-loaded behind the route.
