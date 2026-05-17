# ADR-0011: Health, liveness, and self-checks

## Status
Accepted — 2026-05-15

## Context

Software Architect review F# SA-E.health flagged the absence of a
health-endpoint contract. Without one:

- Docker's `HEALTHCHECK` directive has nothing useful to call.
- s6's service dependency model can't gate `control-plane` on
  `nginx-rtmp` being actually ready (only "started").
- A wedged supervisor (event loop deadlocked but HTTP still answering)
  is silent until a user notices the stream isn't going out.
- A reverse proxy can't tell the difference between "starting" and
  "stuck" on rolling deploys.

The control plane has three components whose readiness matters
independently:

1. **HTTP server** — Uvicorn answering at all.
2. **Persistence** — DB writable, schema version matches.
3. **Ingest** — nginx-rtmp reachable on loopback, accepting connections.
4. **Supervisor loop** — the per-second tick that drains worker progress
   has run within the last few seconds.

Master-passphrase locked state (ADR-0010 paste mode) is a *fifth*
concern: the service is up but not yet ready to accept publishes.

## Decision

**Three endpoints. No auth. Single source of truth: a `HealthReport`
struct serialized to JSON, with a top-level `status` and per-check
detail.**

### `GET /livez`

Returns 200 with body `{"status":"alive"}` as long as the HTTP server
is up. No checks beyond that.

Used by:
- Docker `HEALTHCHECK CMD curl -fsS http://127.0.0.1:8000/livez || exit 1`.
- Kubernetes liveness probe (if anyone deploys this in k8s — supported
  incidentally, not promoted).

This endpoint must NEVER return non-200 due to a dependency. Failing
liveness causes the container to be killed; we kill only if the HTTP
server itself is wedged.

### `GET /readyz`

Returns 200 only when **all** of the following hold:

- DB read+write health check: `SELECT 1` succeeds and a write to a
  dedicated `health_probe` row succeeds within 100 ms.
- Schema version matches expected (per ADR-0007).
- nginx-rtmp loopback reachable: TCP connect to `127.0.0.1:1935`
  succeeds within 50 ms (a cached result is returned for the last 2 s
  to avoid hammering on every probe).
- Supervisor heartbeat: a `monotonic_ns` value updated by the
  supervisor task each tick is within `STALL_TIMEOUT * 2` of `time.monotonic_ns()`.
- Passphrase is loaded (i.e., we are not in ADR-0010 paste-mode-locked).

Non-200 case returns 503 with a structured body:

```json
{
  "status": "not_ready",
  "checks": {
    "db":            { "ok": true,  "latency_ms": 0.4 },
    "schema":        { "ok": true,  "version": 1 },
    "rtmp_ingest":   { "ok": false, "error": "connection refused" },
    "supervisor":    { "ok": true,  "last_tick_ms_ago": 800 },
    "passphrase":    { "ok": true }
  }
}
```

Used by:
- Reverse proxies (Caddy / nginx in front) for graceful failover.
- s6's `notification-fd` mechanism — the control-plane service writes
  to its notification fd only after `/readyz` would return 200 once.

### `GET /healthz`

Alias for `/readyz`. Many probes default to `/healthz`. The alias has
no extra logic; it serves the same handler.

### Implementation notes

- No auth on these endpoints. They reveal "is the service ready" only;
  no secrets, no identifiers, no version (the version surface lives on
  the authenticated About page, ADR-0011 unrelated).
- The endpoints are exempted from the rate limiter (ADR-0005) — a
  reverse proxy hammering them at 1 Hz must not lock anyone out.
- The DB write probe writes to a single row with the supervisor's
  monotonic tick value — cheap, repeatable, and acts as a canary for
  WAL contention.
- The probes themselves are bounded: an unreachable nginx returns "not
  ready" within 50 ms, not after a 30 s default TCP timeout. The
  supervisor task that's tracked is in the same process and is checked
  via a shared atomic value, not via an internal HTTP request.

## Consequences

**Easier:**
- Docker `HEALTHCHECK` finally does something useful.
- s6 dependency ordering works correctly.
- A wedged supervisor surfaces as a `not_ready` signal within seconds.
- Diagnostics: a user reports "panel says we're live but no targets
  go out" — first thing to check is `/readyz`, which immediately tells
  whether the supervisor is alive.

**Harder:**
- The supervisor must publish a heartbeat. That's two lines: an
  `atomic_int64` per task group, updated each iteration.
- Three endpoints to keep aligned. The shared `HealthReport` struct
  prevents drift.

**Rejected alternatives:**

- **One endpoint that returns 200 if HTTP is up** — useless; could
  hide a wedged supervisor.
- **Prometheus metrics endpoint** — different layer of concern. Could
  be added later under `/metrics`. Out of scope for v1.
- **Mix auth into `/readyz`** — breaks probe usage. The endpoint must
  be reachable by Docker / k8s / reverse proxies that don't have
  credentials.

## Open questions / revisit triggers

- If we ever add `/metrics` for Prometheus, `/readyz`'s per-check
  detail might be expanded with histograms.
- If the supervisor grows N parallel task groups (it shouldn't — see
  ADR-0001 amended), each gets a heartbeat slot.
- **Dashboard CPU% and aggregate Mbps are surfaced via the existing
  WS event stream (`host.stats` envelope from
  `app/fanout/host_stats.py`), not `/metrics`.** Three reasons:
  (a) the dashboard is the only consumer and already drains the bus
  via `ws_broadcaster`, so a parallel transport doubles the source of
  truth; (b) Prometheus-style pulls need a separate auth posture (no
  cookie, IP allowlist or bearer token) versus the WS cookie path,
  introducing one more auth surface to harden; (c) adding `/metrics`
  would split the canonical shape of `HostStatsEvent` between bus +
  scrape, forcing a serialization-shape ADR we don't need yet. Add
  `/metrics` only when a Grafana wiring is concrete, not speculative.
  The host-stats sampler reads `/proc/<pid>/stat` directly (no
  `psutil` dep) and polls nginx-rtmp `/rtmp_stat` on loopback for
  ingest bitrate; both are best-effort and degrade silently to `null`
  rather than crashing the sampler loop.
