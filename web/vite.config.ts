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
    // Pure-function tests only — no DOM, no React. The wsReducer is
    // the highest-risk piece in Phase 8 (phase-8-design-memo §M) and
    // is testable as a pure function. Component tests are deferred
    // to a future Phase 8b.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
