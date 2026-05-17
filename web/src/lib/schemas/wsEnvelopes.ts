import { z } from "zod";

import { AwareDatetime } from "./_shared";
import {
  RunState,
  RunStateView,
  TargetUiState,
  WorkerSnapshotView,
} from "./run";

/**
 * Per-event `data` schemas for the four known WS events. The envelope
 * shell (`{v: 1, event, data}`) is already validated by `lib/ws.ts`'s
 * `isEnvelope`; this layer parses the inner shape so cache patching
 * never trusts the wire.
 *
 * Unknown events (`event` strings not in the discriminated union below)
 * are forward-compat — `WsKnownEvent.safeParse` returns `success: false`
 * and the patcher drops them silently.
 */

export const StateFullData = RunStateView;
export type StateFullDataT = z.infer<typeof StateFullData>;

export const RunStateChangedData = z.object({
  new_state: RunState,
  previous_state: RunState,
  cause: z.string(),
  at: AwareDatetime,
});
export type RunStateChangedDataT = z.infer<typeof RunStateChangedData>;

export const TargetSnapshotData = z.object({
  target_id: z.string(),
  ui_state: TargetUiState,
  snapshots_by_role: z.array(WorkerSnapshotView),
  at: AwareDatetime,
});
export type TargetSnapshotDataT = z.infer<typeof TargetSnapshotData>;

export const DropAlertData = z.object({
  dropped_total: z.number().int(),
  at: AwareDatetime,
});
export type DropAlertDataT = z.infer<typeof DropAlertData>;

export const WsKnownEvent = z.discriminatedUnion("event", [
  z.object({ event: z.literal("state.full"), data: StateFullData }),
  z.object({ event: z.literal("run.state.changed"), data: RunStateChangedData }),
  z.object({ event: z.literal("target.snapshot"), data: TargetSnapshotData }),
  z.object({ event: z.literal("bus.drop_alert"), data: DropAlertData }),
]);
export type WsKnownEventT = z.infer<typeof WsKnownEvent>;
