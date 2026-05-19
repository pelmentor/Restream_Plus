import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// React Testing Library mounts into the shared jsdom document; without
// cleanup() between tests, leftover trees leak focus, ARIA live-region
// content, and `getByRole` matches across test boundaries. Vitest's
// auto-cleanup hook didn't fire reliably on `@testing-library/react`
// v16 across all our describe shapes, so we explicitly hook afterEach
// here.
afterEach(() => {
  cleanup();
});
