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
        <Route index element={<Navigate to="general" replace />} />
        <Route path="general" element={<GeneralTab />} />
        <Route path="security" element={<SecurityTab />} />
        <Route path="sessions" element={<SessionsTab />} />
        <Route path="about" element={<AboutTab />} />
        <Route
          path="targets"
          element={<Navigate to="twitch" replace />}
        />
        <Route path="targets/twitch" element={<TwitchTab />} />
        <Route path="targets/youtube" element={<YouTubeTab />} />
        <Route path="targets/kick" element={<KickTab />} />
        <Route path="targets/vk" element={<VKTab />} />
        <Route path="targets/custom" element={<CustomTab />} />
        <Route path="*" element={<Navigate to="general" replace />} />
      </Route>
    </Routes>
  );
}
