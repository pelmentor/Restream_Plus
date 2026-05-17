import { z } from "zod";

export const CheckResult = z.object({
  ok: z.boolean(),
  detail: z.record(z.string(), z.unknown()).default({}),
});
export type CheckResultT = z.infer<typeof CheckResult>;

export const HealthResponse = z.object({
  status: z.enum(["ready", "not_ready", "alive"]),
  checks: z.record(z.string(), CheckResult).default({}),
});
export type HealthResponseT = z.infer<typeof HealthResponse>;
