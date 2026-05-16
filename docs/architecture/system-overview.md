# Restream_Plus — System Overview

A self-hosted RTMP restreamer: a single feed pushed from OBS is fanned out to
Kick, Twitch, YouTube and VK Video Live, controlled from a web panel.

This document is the load-bearing architectural map. Detailed decisions live in
`ADR-*.md` files next to this one — read those for *why* each choice was made.

## 1. Scope and non-goals

**In scope (v1):**
- Accept exactly one RTMP push from OBS over the LAN/host network.
- Fan out the live stream to up to N user-configured targets (Twitch, YouTube,
  Kick, VK Live by default; the model is generic enough that any RTMP/RTMPS
  target works).
- Web panel: a Dashboard with a single big START/STOP control, per-target
  enable/disable toggles, live status (uptime, bitrate, dropped frames per
  target), and a Settings area with sections per target plus general server
  settings.
- Stream-key storage with at-rest encryption tied to a master passphrase
  supplied on container start.
- Single-tenant authentication (one admin user, password-protected web panel,
  long-lived API token for automation).
- One Docker image, pullable from GHCR, with everything inside (ingest,
  control plane, fan-out workers, static frontend).

**Out of scope (explicitly, v1):**
- Transcoding / re-encoding. The fan-out is **passthrough** — `ffmpeg
  -c copy`. Per-target bitrate adjustment would require a transcoding tier
  and is deferred. Targets either accept the OBS-supplied codec/bitrate or
  they are misconfigured upstream.
  - **Encoding is OBS's job**, done on the user's GPU (the target deployment
    assumes a modern consumer NVENC/AMF/QuickSync — e.g., RTX 5060). The
    relay's value is fan-out and control, not CPU/GPU work. This is the
    foundational design constraint that makes the whole system feasible on
    a small VPS or NAS.
- Multi-user accounts, RBAC, OAuth/OIDC integration.
- Recording / VOD storage.
- Inbound from sources other than RTMP (no SRT, no WebRTC, no HLS ingest).
- Auto-scaling, multi-node clustering, region failover.

The product is a "appliance for one streamer". Doing more would violate the
software architect rule against unnecessary complexity (see ADR-0001).

## 2. Bounded contexts and components (C4 — container view)

```
                    ┌──────────────────────────────────────────┐
                    │  Browser (user's machine, LAN)           │
                    │  ┌────────────────────────────────────┐  │
                    │  │  Web Panel (React SPA)             │  │
                    │  └────────────┬───────────────────────┘  │
                    └───────────────┼──────────────────────────┘
                                    │ HTTPS (reverse-proxy
                                    │   terminates TLS if any)
                                    │ /api/*  + /ws  (WebSocket)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Restream_Plus container (single image)                                  │
│                                                                          │
│  ┌────────────────────┐  ┌─────────────────────────────────────┐        │
│  │   nginx-rtmp        │  │  Control Plane (FastAPI, uvicorn)   │        │
│  │   - listens :1935   │  │  - REST under /api/*                │        │
│  │   - on_publish hook │◀─┤  - WebSocket under /ws (status)     │        │
│  │   - on_publish_done │  │  - serves built frontend statically │        │
│  │   - exposes stats   │  │  - auth, settings CRUD,             │        │
│  │     XML at :8080    │─▶│    target CRUD, run-state           │        │
│  └─────────┬──────────┘  │  - supervises ffmpeg workers         │        │
│            │             └────────────────┬─────────────────────┘        │
│            │ local RTMP pull              │ spawns / signals             │
│            │ rtmp://127.0.0.1/live/in     │ asyncio.subprocess           │
│            ▼                              ▼                              │
│  ┌────────────────────────────────────────────────────────────┐         │
│  │  Fan-out workers (one ffmpeg per target, supervised)        │         │
│  │  ffmpeg -i rtmp://127.0.0.1/live/in -c copy -f flv          │         │
│  │         rtmps://<target>/<key>                              │         │
│  │  - reads structured progress from ffmpeg -progress pipe     │         │
│  │  - reports bitrate / fps / dropped frames over IPC          │         │
│  │  - exponential backoff on disconnect (target-side)          │         │
│  └────────────────────────────────────────────────────────────┘         │
│                                                                          │
│  ┌────────────────────┐  ┌─────────────────────────────────────┐        │
│  │   SQLite (file)    │  │  Process supervisor: s6-overlay     │        │
│  │   - settings       │  │  - nginx, control-plane,            │        │
│  │   - targets        │  │    log rotation                     │        │
│  │   - audit log      │  │  - graceful shutdown on SIGTERM     │        │
│  │   secrets:         │  │                                     │        │
│  │   AES-256-GCM enc. │  │  (ffmpeg workers are owned by the   │        │
│  │   key derived from │  │   control plane, NOT by s6 — they   │        │
│  │   MASTER_PASSPHRASE│  │   need fine-grained restart logic)  │        │
│  └────────────────────┘  └─────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ outbound RTMPS to platform ingest
                                    ▼
        ┌──────────┬──────────┬──────────┬──────────────┐
        │  Twitch  │ YouTube  │   Kick   │ VK Video Live│
        └──────────┴──────────┴──────────┴──────────────┘
```

### Domain language

- **Ingest**: the single RTMP entry point that OBS pushes to. Identified by
  a server-side "ingest key" — a randomly generated string that acts as both
  the RTMP stream name and a shared secret. If OBS pushes with the wrong key,
  nginx-rtmp's `on_publish` hook rejects the connection. The threat-model
  trade-offs around log discipline and future RTMPS-from-OBS are covered in
  ADR-0003.
- **Target**: a configured output destination (Twitch, YouTube, Kick, VK Live,
  or any custom RTMP/RTMPS URL). Owns URL, type, label, and an
  enabled/disabled flag. A Target has 1..N Workers at runtime (e.g. YouTube
  with primary+backup ingest is 2 Workers, one Target). See ADR-0008.
- **Credential**: a separate entity (per ADR-0009) carrying the encrypted
  stream key (or future refresh-token), with an explicit `lifetime`
  policy: `persistent` (Twitch/YouTube/Kick/custom) or `per_session`
  (VK Live — collected at START, wiped at END).
- **Worker**: one outbound push of the loopback RTMP feed to one URL. The
  abstraction is defined by a `Worker` protocol; the v1 implementation is
  `FFmpegWorker`. See ADR-0008.
- **Three state types** (per F# SA-A2 — never collapsed into one):
  - `RunState` (system-wide): `offline | starting | armed | live |
    stopping | error`. Toggled by START/STOP.
  - `TargetEnabled` (per-target policy): `enabled | disabled`.
  - `WorkerState` (per-Worker runtime): `idle | starting | running |
    reconnecting | failed_open | stopping`.
- **Session**: a contiguous period during which OBS is publishing AND the
  RunState is `live`. Sessions are persisted in `sessions_history` with
  start/end timestamps and per-target outcome (per ADR-0007).

## 3. Data flow

1. User clicks **START** in the Dashboard.
   - Web panel calls `POST /api/run/start`.
   - Control plane sets run state = active, writes audit entry, broadcasts
     state change on `/ws`.
   - For every target where `enabled = true`, the control plane spawns an
     ffmpeg worker. The workers wait for source data — they connect to
     `rtmp://127.0.0.1/live/in` immediately.
2. User starts OBS streaming. OBS pushes to
   `rtmp://<server-host>:1935/live/<ingest-key>`.
3. nginx-rtmp's `on_publish` hook fires a webhook to the control plane:
   `POST /internal/rtmp/publish`. The control plane validates the key and
   either accepts (HTTP 2xx) or rejects (HTTP 4xx → nginx drops the
   connection).
4. ffmpeg workers see frames arriving on the local RTMP pull and start
   pushing to their targets. They emit a `progress` line every second over
   stdout, parsed by the control plane.
5. Control plane broadcasts per-target status updates to all WebSocket
   subscribers every second. The Dashboard re-renders live tiles.
6. User clicks **STOP** in the Dashboard *or* stops OBS publishing.
   - STOP: control plane SIGTERMs all workers (10s grace then SIGKILL),
     records session-end audit entry.
   - OBS stop: nginx-rtmp `on_publish_done` fires, control plane decides
     whether to keep workers alive (so the user can resume OBS without
     re-clicking START — bounded by an idle timeout, default 60s) or tear
     them down.

## 4. Failure modes (selected)

| Failure                                | Behavior                                                                                                                                                                                                                                          |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Target rejects connection (bad key)    | Worker exits non-zero; supervisor records error, marks target unhealthy, exponential backoff (1s, 2s, 4s, … capped at 60s, jittered ±20%). After 30 failed attempts in 5 min: circuit-breaker trips, Worker → `failed_open`. User-resume only. |
| Target drops mid-session               | Same as above per-Worker; other Workers and other Targets unaffected.                                                                                                                                                                            |
| OBS disconnects                        | nginx fires `on_publish_done`. RunState transitions `live → armed`. Workers stay armed for short idle (default 60s); on longer idle (default 300s), transition to `stopping`.                                                                  |
| Control plane crashes                  | s6 restarts it. Children of control plane receive their SIGTERM via the explicit shutdown hook (ADR-0003). On restart, supervisor finds an interrupted `sessions_history` row and writes a `interrupted_session_recovered` audit entry.        |
| OBS publishes twice (reconnect race)   | `on_publish` rejects the second concurrent publish on the same ingest key (ADR-0003 amended). First wins; second receives an error from nginx-rtmp.                                                                                              |
| nginx crashes                          | s6 restarts it. All Workers hit reconnect loops until ingest is back. No data loss because nothing was buffered.                                                                                                                                  |
| Disk full                              | SQLite refuses writes; control plane surfaces a banner; ingest still works at the network level but `on_publish` returns 503 (DB write failed = stateful operation must succeed).                                                                |
| Master passphrase missing on container | Per ADR-0010: in `env` mode, control plane refuses to start with a clear error. In `paste` mode, container enters locked state — HTTP up, but ingest returns 503 until user pastes via web UI.                                                  |
| Supervisor task wedged                 | Heartbeat ages > 10s; `/readyz` returns 503 with the supervisor check failing (ADR-0011). Operator-visible signal.                                                                                                                              |
| WebSocket consumer too slow            | Supervisor → API queue drops oldest events (bounded queue, ADR-0003 amended). Drop counter surfaces in `/readyz`.                                                                                                                                |

## 5. Quality attributes targeted

- **Latency added by the relay**: ≤ 1s extra glass-to-glass over a direct OBS-
  to-Twitch push, measured under -c copy (no transcode). Latency budget is
  dominated by the platforms, not by us.
- **Control plane API p95**: < 100ms for everything except the start/stop
  endpoints (which are bounded by worker spawn time, ~500ms).
- **Uptime of the container itself**: the container should survive process
  crashes (s6 restart). Network outages are not in our problem space.
- **Security**: stream keys are never logged. They never appear in the
  HTTP API response in plaintext after creation — only metadata + last 4
  chars for confirmation.

## 6. What this document does NOT cover

- Specific ADR rationales — see `ADR-000X-*.md`.
- UI/UX flows — see `../ui/ux-flows.md` and `../ui/design-system.md`.
- Deployment / GHCR mechanics — see `../ops/deployment.md`.
- API contract details — see `../api/openapi.yaml` (generated from FastAPI).
