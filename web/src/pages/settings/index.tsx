/**
 * Lazy-loaded entry for `/settings/*`. The router imports this default
 * export as ONE chunk (phase-9-design-memo §B / §U.17).
 */
import { type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AboutTab } from "./AboutTab";
import { GeneralTab } from "./GeneralTab";
import { SecurityTab } from "./SecurityTab";
import { SessionsTab } from "./SessionsTab";
import { SettingsShell } from "./SettingsShell";
import { CustomTab } from "./targets/CustomTab";
import { KickTab } from "./targets/KickTab";
import { TwitchTab } from "./targets/TwitchTab";
import { VKTab } from "./targets/VKTab";
import { YouTubeTab } from "./targets/YouTubeTab";

export default function SettingsRoutes(): ReactNode {
  return (
    <Routes>
      <Route element={<SettingsShell />}>
        <Route index element={<Navigate to="/settings/general" replace />} />
        <Route path="general" element={<GeneralTab />} />
        <Route path="security" element={<SecurityTab />} />
        <Route path="sessions" element={<SessionsTab />} />
        <Route path="about" element={<AboutTab />} />
        <Route
          path="targets"
          element={<Navigate to="/settings/targets/twitch" replace />}
        />
        <Route path="targets/twitch" element={<TwitchTab />} />
        <Route path="targets/youtube" element={<YouTubeTab />} />
        <Route path="targets/kick" element={<KickTab />} />
        <Route path="targets/vk" element={<VKTab />} />
        <Route path="targets/custom" element={<CustomTab />} />
        {/* Absolute target prevents infinite redirect loops: relative
         * `to="general"` would resolve against the splat-matched URL
         * (e.g. `/settings/foo` → `/settings/foo/general`), and that
         * path re-triggers the splat → `/settings/foo/general/general`
         * → compounds until the browser/OS path-length limit crashes
         * the SPA fallback. Same reasoning for the other Navigate
         * elements above and for the sidebar NavLinks.
         */}
        <Route path="*" element={<Navigate to="/settings/general" replace />} />
      </Route>
    </Routes>
  );
}
