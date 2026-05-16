/* eslint-disable react-refresh/only-export-components -- the provider co-locates its consumer hooks; splitting into a separate `useRecentEvents.ts` would create a cycle */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";

/**
 * In-memory store for the RecentEventsMenu (design-system §6.21,
 * phase-8-design-memo §O). React Context + reducer; mounted inside
 * RequireAuth so AppShell (the bell) and StatusStreamHost (the
 * producer) share one source. Capped at 50 entries.
 */

export type RecentEventKind = "info" | "warn" | "error" | "success";

export interface RecentEvent {
  readonly id: string;
  readonly kind: RecentEventKind;
  readonly message: string;
  readonly at: Date;
}

interface RecentEventsState {
  readonly events: readonly RecentEvent[];
  readonly unseenCount: number;
}

type RecentEventsAction =
  | { type: "push"; event: RecentEvent }
  | { type: "markSeen" }
  | { type: "clear" };

const MAX_EVENTS = 50;

const INITIAL: RecentEventsState = { events: [], unseenCount: 0 };

function reducer(state: RecentEventsState, action: RecentEventsAction): RecentEventsState {
  switch (action.type) {
    case "push": {
      const events = [action.event, ...state.events].slice(0, MAX_EVENTS);
      return { events, unseenCount: state.unseenCount + 1 };
    }
    case "markSeen":
      if (state.unseenCount === 0) return state;
      return { ...state, unseenCount: 0 };
    case "clear":
      return INITIAL;
  }
}

interface RecentEventsContextValue {
  readonly state: RecentEventsState;
  readonly dispatch: Dispatch<RecentEventsAction>;
}

const RecentEventsContext = createContext<RecentEventsContextValue | null>(null);

export function RecentEventsProvider({ children }: { children: ReactNode }): ReactNode {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const value = useMemo(() => ({ state, dispatch }), [state]);
  return <RecentEventsContext.Provider value={value}>{children}</RecentEventsContext.Provider>;
}

export function useRecentEvents(): RecentEventsState {
  const ctx = useContext(RecentEventsContext);
  if (ctx === null) {
    throw new Error("useRecentEvents must be used inside <RecentEventsProvider>");
  }
  return ctx.state;
}

export function useRecentEventsActions(): {
  push: (event: RecentEvent) => void;
  markSeen: () => void;
  clear: () => void;
} {
  const ctx = useContext(RecentEventsContext);
  if (ctx === null) {
    throw new Error("useRecentEventsActions must be used inside <RecentEventsProvider>");
  }
  const { dispatch } = ctx;
  const push = useCallback((event: RecentEvent) => dispatch({ type: "push", event }), [dispatch]);
  const markSeen = useCallback(() => dispatch({ type: "markSeen" }), [dispatch]);
  const clear = useCallback(() => dispatch({ type: "clear" }), [dispatch]);
  return { push, markSeen, clear };
}
