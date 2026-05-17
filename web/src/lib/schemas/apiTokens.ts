import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const ApiTokenView = z.object({
  id: z.string(),
  label: z.string(),
  created_at: AwareDatetime,
  last_used_at: AwareDatetime.nullable(),
  revoked_at: AwareDatetime.nullable(),
});
export type ApiTokenViewT = z.infer<typeof ApiTokenView>;

export const ApiTokenList = z.array(ApiTokenView);

export const ApiTokenCreateRequest = z.object({
  label: z.string().min(1).max(128),
});
export type ApiTokenCreateRequestT = z.infer<typeof ApiTokenCreateRequest>;

export const ApiTokenCreatedResponse = z.object({
  plaintext: z.string(),
  token: ApiTokenView,
});
export type ApiTokenCreatedResponseT = z.infer<typeof ApiTokenCreatedResponse>;
