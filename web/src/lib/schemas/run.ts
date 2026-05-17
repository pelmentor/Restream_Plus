import { z } from "zod";

import { AwareDatetime } from "./_shared";

export const RunState = z.enum([
  "offline",
  "starting",
  "armed",
  "live",
  "stopping",
  "error",
]);
export type RunStateT = z.infer<typeof RunState>;

export const TargetUiState = z.enum([
  "disabled",
  "disabled_misconfigured",
  "idle",
  "starting",
  "running",
  "degraded",
  "errored",
  "failed_open",
]);
export type TargetUiStateT = z.infer<typeof TargetUiState>;

export const WorkerState = z.enum([
  "idle",
  "starting",
  "running",
  "reconnecting",
  "failed_open",
  "stopping",
]);
export type WorkerStateT = z.infer<typeof WorkerState>;

export const WorkerRole = z.enum(["primary", "backup"]);
export type WorkerRoleT = z.infer<typeof WorkerRole>;

export const WorkerSnapshotView = z.object({
  role: WorkerRole,
  state: WorkerState,
  last_event_at: AwareDatetime,
  last_error: z.string().nullable(),
  breaker_failures_in_window: z.number().int(),
});
export type WorkerSnapshotViewT = z.infer<typeof WorkerSnapshotView>;

export const TargetSnapshot = z.object({
  target_id: z.string(),
  ui_state: TargetUiState,
  snapshots_by_role: z.array(WorkerSnapshotView).readonly(),
});
export type TargetSnapshotT = z.infer<typeof TargetSnapshot>;

export const RunStateView = z.object({
  run_state: RunState,
  targets: z.array(TargetSnapshot).readonly(),
  heartbeat_age_seconds: z.number(),
  dropped_total: z.number().int(),
  run_state_changed_at: AwareDatetime.nullable(),
});
export type RunStateViewT = z.infer<typeof RunStateView>;

export const RunActionAcceptedResponse = z.object({
  accepted: z.literal(true),
  previous_state: RunState,
});
export type RunActionAcceptedResponseT = z.infer<
  typeof RunActionAcceptedResponse
>;
