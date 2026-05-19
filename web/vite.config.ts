import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The dev server proxies API + WS to the FastAPI control plane so frontend
// devs can run `npm run dev` and the backend on :8000 and have the SPA at
// :5173 behave exactly as it would when served from the same origin in prod.
const BACKEND_URL = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    // Bind to 0.0.0.0 so dev with HMR is reachable from any device on
    // the LAN (operator tests the panel from a second laptop / phone
    // by hitting `http://<dev-box-LAN-IP>:5173/`). Without this Vite
    // binds to 127.0.0.1 only, the LAN device falls back to the
    // backend's `_spa_fallback` at :8000 which serves the LAST-BUILT
    // `web/dist/` bundle — a stale-bundle footgun that masked v1.1.3
    // + the post-v1.1.3 UX fixes from operator smoke-tests until
    // someone noticed and ran `npm run build`.
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND_URL, changeOrigin: false },
      "/ws": { target: BACKEND_URL, ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "es2022",
    cssMinify: "lightningcss",
    rollupOptions: {
      output: {
        // Manual chunks keep the Dashboard tiny on cold load —
        // Settings is lazy-loaded by the router anyway.
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router-dom"],
          "radix": [
            "@radix-ui/react-dialog",
            // Slice-6 reviewer M-3: dropdown-menu added when AccountMenu
            // migrated from <details>/<summary>; keep it in the shared
            // "radix" chunk so AppShell doesn't bloat the critical path.
            "@radix-ui/react-dropdown-menu",
            // Slice-10 Radix RecentEventsMenu migration: popover added
            // when the bell-popup migrated from a hand-rolled
            // disclosure. Same chunk-rationale as react-dropdown-menu:
            // AppShell mounts the menu, so the bundle wants to ship it
            // alongside the other already-loaded Radix primitives.
            "@radix-ui/react-popover",
            "@radix-ui/react-radio-group",
            "@radix-ui/react-switch",
            "@radix-ui/react-tabs",
            "@radix-ui/react-toast",
            "@radix-ui/react-tooltip",
          ],
        },
      },
    },
  },
  test: {
    // Slice-6 SA-RISK-4: jsdom + @testing-library upgrade.
    // The slice-4 CRIT-1 (silent Tailwind cascade-order shadow that
    // turned Revoke/Delete buttons gray instead of red) was render-
    // observable but invisible to pure-function tests. Component
    // tests now run in jsdom; pure-function tests (`*.test.ts`)
    // are unaffected because jsdom is a superset of node for code
    // that doesn't touch `document`. Run with `npm test`.
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Vitest already supports `describe`/`it`/`expect` via direct import
    // (matches our codebase convention). `globals: true` would only be
    // load-bearing if we wanted to skip imports — keep them explicit.
    css: false,
  },
});
