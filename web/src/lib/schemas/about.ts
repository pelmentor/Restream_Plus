import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const AboutView = z.object({
  version: z.string(),
  build_sha: z.string(),
  started_at: AwareDatetime,
  uptime_seconds: z.number(),
  last_reboots: z.array(AwareDatetime),
});
export type AboutViewT = z.infer<typeof AboutView>;
