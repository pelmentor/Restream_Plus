import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const SettingsView = z.object({
  first_run_complete: z.boolean(),
  idle_timeout_seconds: z.number().int(),
  log_retention_days: z.number().int(),
  ingest_key_last4: z.string(),
  ingest_key_has_previous: z.boolean(),
  ingest_key_rotated_at: AwareDatetime.nullable(),
  ingest_key_grace_until: AwareDatetime.nullable(),
  updated_at: AwareDatetime,
});
export type SettingsViewT = z.infer<typeof SettingsView>;

export const SettingsUpdateRequest = z.object({
  idle_timeout_seconds: z.number().int().min(30).max(86400).optional(),
  log_retention_days: z.number().int().min(1).max(365).optional(),
});
export type SettingsUpdateRequestT = z.infer<typeof SettingsUpdateRequest>;

export const IngestKeyRotatedResponse = z.object({
  plaintext: z.string(),
  last4: z.string(),
  grace_until: AwareDatetime,
});
export type IngestKeyRotatedResponseT = z.infer<typeof IngestKeyRotatedResponse>;

export const IngestKeyRevealResponse = z.object({
  plaintext: z.string(),
  last4: z.string(),
});
export type IngestKeyRevealResponseT = z.infer<typeof IngestKeyRevealResponse>;

export const RevealCredentialResponse = z.object({
  plaintext: z.string(),
  last4: z.string(),
});
export type RevealCredentialResponseT = z.infer<typeof RevealCredentialResponse>;
