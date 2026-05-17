import { z } from "zod";

import { AwareDatetime } from "./_shared";

// HTTP sessions (Phase 9 — GET /api/security/sessions).

export const HttpSessionView = z.object({
  id_fingerprint: z.string(),
  created_at: AwareDatetime,
  last_seen_at: AwareDatetime,
  ip: z.string().nullable(),
  user_agent: z.string().nullable(),
  is_current: z.boolean(),
});
export type HttpSessionViewT = z.infer<typeof HttpSessionView>;

export const HttpSessionList = z.array(HttpSessionView);

// Run-history (GET /api/sessions — exists since Phase 6).

export const RunHistoryItem = z.object({
  id: z.string(),
  started_at: AwareDatetime,
  ended_at: AwareDatetime.nullable(),
  end_reason: z.string().nullable(),
  duration_seconds: z.number().nullable(),
});
export type RunHistoryItemT = z.infer<typeof RunHistoryItem>;

export const RunHistoryPage = z.object({
  items: z.array(RunHistoryItem),
  next_before: AwareDatetime.nullable(),
});
export type RunHistoryPageT = z.infer<typeof RunHistoryPage>;
