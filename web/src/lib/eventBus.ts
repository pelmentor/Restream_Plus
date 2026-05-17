/**
 * Tiny string-keyed event emitter.
 *
 * Phase 7 uses it for ONE thing: `apiFetch` errors that the top-level
 * `App` subscriber needs to react to (401 → /login, 503-locked →
 * /unlock). `apiFetch` itself stays pure (no React, no router); the
 * bus is the seam.
 *
 * Listeners receive the value; subscribe returns an unsubscribe.
 * Synchronous dispatch — emit() blocks until every listener runs (small
 * N, fine).
 */

type Listener<T> = (value: T) => void;

export interface EventEmitter<T> {
  emit(value: T): void;
  subscribe(listener: Listener<T>): () => void;
}

export function createEmitter<T>(): EventEmitter<T> {
  const listeners = new Set<Listener<T>>();
  return {
    emit(value) {
      listeners.forEach((l) => {
        try {
          l(value);
        } catch {
          /* a misbehaving listener must not break the bus */
        }
      });
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
