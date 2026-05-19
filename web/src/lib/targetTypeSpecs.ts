/**
 * Frontend mirror of `app/domain/target_types.py::TARGET_TYPE_SPECS`.
 *
 * The four named-platform tabs (Twitch / YouTube / Kick / VK) drive
 * their preset-URL dropdown from this table; the Custom tab uses a
 * free-text URL only. Backend is the canonical source of truth for
 * validation; this file is UX convenience for the preset picker.
 *
 * Per phase-9-design-memo §H: YouTube backup-ingest as a frontend
 * toggle is OUT of Phase 9 (worker model can't express "one target,
 * two workers" yet — Phase 10 ADR).
 */

import type { TargetTypeT } from "./schemas/targets";

export interface TargetTypeSpec {
  readonly displayLabel: string;
  readonly defaultUrl: string;
  readonly presetUrls: readonly string[];
  readonly defaultLabel: string;
  /** True if a stream-key field is shown on the form (false for VK). */
  readonly persistentStreamKey: boolean;
  /** Hint copy under the URL field; empty string for none. */
  readonly urlHint: string;
}

// Twitch regional URLs verbatim from OBS `services.json`
// (https://github.com/obsproject/obs-studio/blob/master/plugins/rtmp-services/data/services.json).
// `rtmp://live.twitch.tv/app` is Twitch's "intelligent ingest" hostname
// that OBS doesn't ship — it historically smart-routes to the nearest
// regional server, but in practice that routing is occasionally broken
// (operator-observed 2026-05-18: every push to live.twitch.tv/app
// fails with `process exited unexpectedly`; a regional URL works).
// We keep `live.twitch.tv/app` as the default for the auto-routing
// case AND ship the full 46-region OBS list so the operator can pick
// one when smart routing fails.
const TWITCH_PRESET_URLS = [
  "rtmp://live.twitch.tv/app",
  // Asia
  "rtmp://live-hkg.twitch.tv/app",
  "rtmp://live-sel.twitch.tv/app",
  "rtmp://live-sin.twitch.tv/app",
  "rtmp://live-tpe.twitch.tv/app",
  "rtmp://live-tyo.twitch.tv/app",
  // Australia
  "rtmp://live-syd.twitch.tv/app",
  // EU
  "rtmp://live-ams.twitch.tv/app",
  "rtmp://live-ber.twitch.tv/app",
  "rtmp://live-cph.twitch.tv/app",
  "rtmp://live-fra.twitch.tv/app",
  "rtmp://live-hel.twitch.tv/app",
  "rtmp://live-lis.twitch.tv/app",
  "rtmp://live-lhr.twitch.tv/app",
  "rtmp://live-mad.twitch.tv/app",
  "rtmp://live-mrs.twitch.tv/app",
  "rtmp://live-mil.twitch.tv/app",
  "rtmp://live-osl.twitch.tv/app",
  "rtmp://live-cdg.twitch.tv/app",
  "rtmp://live-prg.twitch.tv/app",
  "rtmp://live-arn.twitch.tv/app",
  "rtmp://live-vie.twitch.tv/app",
  "rtmp://live-waw.twitch.tv/app",
  // NA
  "rtmp://live-qro.twitch.tv/app",
  "rtmp://live-ymq.twitch.tv/app",
  "rtmp://live-yto.twitch.tv/app",
  // South America
  "rtmp://live-eze.twitch.tv/app",
  "rtmp://live-scl.twitch.tv/app",
  "rtmp://live-lim.twitch.tv/app",
  "rtmp://live-mde.twitch.tv/app",
  "rtmp://live-rio.twitch.tv/app",
  "rtmp://live-sao.twitch.tv/app",
  // US Central
  "rtmp://live-dfw.twitch.tv/app",
  "rtmp://live-den.twitch.tv/app",
  "rtmp://live-hou.twitch.tv/app",
  "rtmp://live-slc.twitch.tv/app",
  // US East
  "rtmp://live-iad.twitch.tv/app",
  "rtmp://live-atl.twitch.tv/app",
  "rtmp://live-ord.twitch.tv/app",
  "rtmp://live-mia.twitch.tv/app",
  "rtmp://live-jfk.twitch.tv/app",
  // US West
  "rtmp://live-lax.twitch.tv/app",
  "rtmp://live-phx.twitch.tv/app",
  "rtmp://live-pdx.twitch.tv/app",
  "rtmp://live-sfo.twitch.tv/app",
  "rtmp://live-sjc.twitch.tv/app",
  "rtmp://live-sea.twitch.tv/app",
] as const;

export const TARGET_TYPE_SPECS: Record<TargetTypeT, TargetTypeSpec> = {
  twitch: {
    displayLabel: "Twitch",
    defaultUrl: "rtmp://live.twitch.tv/app",
    presetUrls: TWITCH_PRESET_URLS,
    defaultLabel: "Twitch — main channel",
    persistentStreamKey: true,
    urlHint:
      "Default is Twitch's auto-routing endpoint. If pushes fail, " +
      "pick a regional URL closer to you from the list.",
  },
  youtube: {
    displayLabel: "YouTube",
    defaultUrl: "rtmps://a.rtmps.youtube.com:443/live2",
    // Full set verbatim from OBS services.json. Backup URLs carry the
    // `?backup=1` query param exactly as OBS configures them — required
    // by YouTube to route the second connection as a hot failover.
    presetUrls: [
      "rtmps://a.rtmps.youtube.com:443/live2",
      "rtmps://b.rtmps.youtube.com:443/live2?backup=1",
      // Legacy RTMP (no TLS). Kept because some operators behind
      // RTMPS-hostile middleboxes still rely on these.
      "rtmp://a.rtmp.youtube.com/live2",
      "rtmp://b.rtmp.youtube.com/live2?backup=1",
    ],
    defaultLabel: "YouTube Live",
    persistentStreamKey: true,
    urlHint:
      "Primary endpoint is `a`; `b` is YouTube's hot backup. " +
      "Legacy `rtmp://` variants are available if RTMPS is blocked.",
  },
  kick: {
    displayLabel: "Kick",
    defaultUrl: "rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
    presetUrls: [
      "rtmps://fa723fc1b171.global-contribute.live-video.net:443/app",
    ],
    defaultLabel: "Kick",
    persistentStreamKey: true,
    urlHint: "Kick's ingest is hosted on AWS IVS.",
  },
  vk_live: {
    displayLabel: "VK Live",
    defaultUrl: "rtmp://ovsu.okcdn.ru/input/",
    presetUrls: [
      "rtmp://ovsu.okcdn.ru/input/",
      "rtmp://vp.vkforms.ru/live/",
    ],
    defaultLabel: "VK Video Live",
    persistentStreamKey: false,
    urlHint:
      "VK requires a fresh stream key for every broadcast — paste it on the Dashboard.",
  },
  custom: {
    displayLabel: "Custom",
    defaultUrl: "",
    presetUrls: [],
    defaultLabel: "Custom target",
    persistentStreamKey: true,
    urlHint: "Any RTMP / RTMPS ingest URL.",
  },
};
