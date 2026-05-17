/**
 * WebSocket plumbing for the /ws status stream.
 *
 * Phase 7 ships this as a module-singleton class. Phase 8's
 * `useStatusStream` hook will subscribe; the singleton prevents a 10-
 * component subscribe from opening 10 sockets.
 *
 * Reconnect backoff: 1 → 2 → 4 → 8 → 10s cap (ux-flows.md §5).
 * Close codes (app/api/ws.py):
 *   4401  auth_failed  — emit ApiError(unauthorized,401) to globalApiErrorBus
 *   4503  locked       — emit ApiError(service_locked,503)
 *   1011  overload     — silent reconnect with backoff
 *   1006  network drop — silent reconnect with backoff
 *   1000  clean close  — `wantOpen` is false; do not reconnect
 *
 * Envelope: only `{v: 1, event, data}` shapes dispatch; future v ignored.
 *
 * Phase 7 does NOT call `connect()` from anywhere — the singleton is
 * inert. Phase 8 starts it from `<RequireAuth>`'s post-success effect.
 */

import { ApiError, globalApiErrorBus } from "./api";

export type WsConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "auth_failed"
  | "locked"
  | "closed";

export interface WsEnvelope {
  readonly v: 1;
  readonly event: string;
  readonly data: Record<string, unknown>;
}

const RECONNECT_CAP_MS = 10_000;
const RECONNECT_BASE_MS = 1_000;
const WS_PROTOCOL_VERSION = 1;
const WS_CLOSE_AUTH_FAIL = 4401;
const WS_CLOSE_LOCKED = 4503;

type EnvelopeListener = (envelope: WsEnvelope) => void;
type StatusListener = (status: WsConnectionStatus) => void;

export class StatusStream {
  private ws: WebSocket | null = null;
  private wantOpen = false;
  private attempt = 0;
  private reconnectTimer: number | null = null;
  private readonly listeners = new Set<EnvelopeListener>();
  private readonly statusListeners = new Set<StatusListener>();
  private status: WsConnectionStatus = "idle";

  connect(): void {
    this.wantOpen = true;
    if (this.ws !== null) return;
    this.open();
  }

  disconnect(): void {
    this.wantOpen = false;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws !== null) {
      this.ws.close(1000, "client-disconnect");
      this.ws = null;
    }
    this.setStatus("closed");
  }

  subscribe(listener: EnvelopeListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  getStatus(): WsConnectionStatus {
    return this.status;
  }

  private setStatus(next: WsConnectionStatus): void {
    if (this.status === next) return;
    this.status = next;
    this.statusListeners.forEach((l) => {
      try {
        l(next);
      } catch {
        /* swallow listener errors */
      }
    });
  }

  private open(): void {
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
    };

    ws.onmessage = (event) => {
      try {
        const raw: unknown = JSON.parse(event.data as string);
        if (!isEnvelope(raw)) return;
        this.listeners.forEach((l) => {
          try {
            l(raw);
          } catch {
            /* swallow listener errors */
          }
        });
      } catch {
        /* malformed frame */
      }
    };

    ws.onerror = () => {
      /* onclose runs immediately after — handle there */
    };

    ws.onclose = (event) => {
      this.ws = null;
      if (event.code === WS_CLOSE_AUTH_FAIL) {
        this.setStatus("auth_failed");
        globalApiErrorBus.emit(new ApiError({ code: "unauthorized", status: 401 }));
        this.wantOpen = false;
        return;
      }
      if (event.code === WS_CLOSE_LOCKED) {
        this.setStatus("locked");
        globalApiErrorBus.emit(new ApiError({ code: "service_locked", status: 503 }));
        this.wantOpen = false;
        return;
      }
      if (!this.wantOpen) {
        this.setStatus("closed");
        return;
      }
      const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, this.attempt), RECONNECT_CAP_MS);
      this.attempt += 1;
      this.setStatus("reconnecting");
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        if (this.wantOpen) this.open();
      }, delay);
    };
  }
}

function isEnvelope(value: unknown): value is WsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return (
    obj.v === WS_PROTOCOL_VERSION &&
    typeof obj.event === "string" &&
    typeof obj.data === "object" &&
    obj.data !== null
  );
}

export const statusStream = new StatusStream();
