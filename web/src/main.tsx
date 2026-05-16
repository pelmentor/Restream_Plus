import "@/theme/tokens.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { createAppQueryClient } from "@/lib/queryClient";
import { routes } from "@/router";
import { themeManager } from "@/theme/ThemeManager";

themeManager.init();

const queryClient = createAppQueryClient();
const router = createBrowserRouter(routes);

const rootEl = document.getElementById("root");
if (rootEl === null) {
  throw new Error("missing #root element");
}

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
