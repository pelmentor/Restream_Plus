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

export const TARGET_TYPE_SPECS: Record<TargetTypeT, TargetTypeSpec> = {
  twitch: {
    displayLabel: "Twitch",
    defaultUrl: "rtmp://live.twitch.tv/app",
    presetUrls: ["rtmp://live.twitch.tv/app"],
    defaultLabel: "Twitch — main channel",
    persistentStreamKey: true,
    urlHint: "Twitch's RTMP ingest endpoint.",
  },
  youtube: {
    displayLabel: "YouTube",
    defaultUrl: "rtmps://a.rtmps.youtube.com:443/live2",
    presetUrls: [
      "rtmps://a.rtmps.youtube.com:443/live2",
      "rtmps://b.rtmps.youtube.com:443/live2?backup=1",
    ],
    defaultLabel: "YouTube Live",
    persistentStreamKey: true,
    urlHint: "Primary endpoint is `a`; `b` is YouTube's hot backup.",
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
