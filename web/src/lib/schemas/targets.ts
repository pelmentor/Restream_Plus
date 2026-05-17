import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const TargetType = z.enum([
  "twitch",
  "youtube",
  "kick",
  "vk_live",
  "custom",
]);
export type TargetTypeT = z.infer<typeof TargetType>;

export const Target = z.object({
  id: z.string(),
  type: TargetType,
  label: z.string(),
  url: z.string(),
  enabled: z.boolean(),
  settings: z.record(z.string(), z.unknown()),
  has_credential: z.boolean(),
  credential_last4: z.string().nullable(),
  created_at: AwareDatetime,
  updated_at: AwareDatetime,
});
export type TargetT = z.infer<typeof Target>;

export const TargetList = z.array(Target);
export type TargetListT = z.infer<typeof TargetList>;

export const CredentialSummary = z.object({
  last4: z.string(),
  lifetime: z.enum(["persistent", "per_session", "refresh_token"]),
  created_at: AwareDatetime,
  expires_at: AwareDatetime.nullable(),
});
export type CredentialSummaryT = z.infer<typeof CredentialSummary>;

export const TargetCreateRequest = z.object({
  type: TargetType,
  label: z.string().min(1).max(128),
  url: z.string().min(1).max(2048),
  enabled: z.boolean(),
  settings: z.record(z.string(), z.unknown()).default({}),
});
export type TargetCreateRequestT = z.infer<typeof TargetCreateRequest>;

export const TargetUpdateRequest = z.object({
  label: z.string().min(1).max(128).optional(),
  url: z.string().min(1).max(2048).optional(),
  enabled: z.boolean().optional(),
  settings: z.record(z.string(), z.unknown()).optional(),
});
export type TargetUpdateRequestT = z.infer<typeof TargetUpdateRequest>;

export const CredentialSetRequest = z.object({
  stream_key: z.string().min(1).max(4096),
});
export type CredentialSetRequestT = z.infer<typeof CredentialSetRequest>;
