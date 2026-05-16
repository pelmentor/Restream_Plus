# ADR-0001: Backend stack — Python 3.12 + FastAPI

## Status
Accepted — 2026-05-15

## Context

The control plane has to do three things, and they pull in different
directions when picking a language:

1. **Manage long-lived `ffmpeg` subprocesses** — spawn one per target, monitor
   stdout for `-progress` lines, route SIGTERM correctly on shutdown, restart
   with exponential backoff. This is operating-system-level process plumbing.
2. **Serve a small HTTP/WebSocket API** — REST CRUD over targets/settings,
   plus a WebSocket channel that broadcasts per-target metrics every second
   to whoever has the Dashboard open.
3. **Encrypt and persist secrets** — AES-256-GCM around the stream keys, with
   the key derived from a master passphrase supplied at container start.

The project is single-tenant and self-hosted. Throughput is whatever a few
WebSocket clients and ~5 ffmpeg child processes produce — i.e., trivially
small. Developer ergonomics and clean async APIs matter more than raw
runtime speed.

## Decision

**Python 3.12 + FastAPI + Uvicorn**, with `asyncio.subprocess` for ffmpeg
management.

Specific deps:
- `fastapi` (HTTP + WebSocket framework).
- `uvicorn[standard]` (ASGI server; the `standard` extra brings
  `uvloop`/`httptools` for performance and `websockets` for WS).
- `pydantic` v2 (request/response models, settings via `pydantic-settings`).
- `sqlalchemy` 2.x async + `aiosqlite` (persistence — see ADR-0006 context).
- `argon2-cffi` (password hashing — see ADR-0005).
- `cryptography` (AES-256-GCM for stream-key encryption — see ADR-0006).
- `httpx` (outbound HTTP if needed for platform API checks).
- `structlog` (structured JSON logs).

No frameworks beyond these. No Celery (we don't need a task queue — ffmpeg
processes are the only "workers" and they are managed by the asyncio
supervisor in-process). No Redis. No Postgres (see ADR-0006).

## Consequences

**Easier:**
- `asyncio.subprocess` gives us the exact primitive needed for ffmpeg
  supervision — `Process.stdout` is an async stream, `terminate()` /
  `wait()` / `send_signal()` are all first-class, and we can supervise
  N children with one event loop without threads.
- FastAPI's WebSocket support is native and clean. No external pub/sub bus
  needed for fan-out of status updates to a handful of browser clients.
- Pydantic v2 settings + models give us a single source of truth for the
  config schema, including env-var binding and JSON Schema for the OpenAPI
  doc that ships to the frontend.
- The Python ecosystem around streaming/ffmpeg is the largest among the
  candidates — when a quirk arises (e.g., parsing `ffmpeg -progress` output
  reliably), there's prior art.

**Harder:**
- Container will be larger than a Go binary (~150–200 MB compressed vs
  ~30 MB). For a self-hosted appliance the user pulls once, this is
  acceptable. We mitigate with a slim base image (`python:3.12-slim`) and
  multi-stage build.
- Cold start ~1s slower than Go. Irrelevant — the container starts once.
- GIL is a non-issue here: the only CPU-bound work (encryption,
  password-hashing) is implemented in C extensions that release the GIL.

**Rejected alternatives:**
- **Node.js + Fastify + TypeScript**: equally viable. Rejected because the
  ffmpeg-supervision code is more idiomatic in asyncio than in
  `child_process` + EventEmitter, and the security primitives in Python's
  `cryptography` library are better-trodden than `node:crypto` for AES-GCM
  + Argon2 specifically. TypeScript everywhere (frontend + backend) was
  the strongest pro, but per ADR-0002 the frontend is TS regardless, and
  the cognitive cost of two languages is low for a single-developer
  codebase.
- **Go**: smallest binary, best cold-start, true parallelism for free.
  Rejected because writing the request/response DTOs, validation, and
  WebSocket fan-out by hand burns more time than the runtime savings
  return for a project of this scope. If the project ever needs to support
  hundreds of concurrent ingests (which it explicitly does not — see
  system-overview.md §1 non-goals), revisit.
- **Rust (axum)**: same reasoning as Go, more so.

## Open questions / revisit triggers

- **Resolved 2026-05-16 (Phase 5 design memo):** subprocess primitive
  is **stdlib `asyncio.create_subprocess_exec`**, not `anyio.open_process`.
  Reasoning: the wrapping `asyncio.TaskGroup` provides the structured
  cancellation that anyio would otherwise add, and stdlib means one
  less abstraction to type-check / one less place a future maintainer
  has to look. anyio is still pulled transitively (Starlette uses it,
  and Phase 5's `Worker.events()` channel uses
  `anyio.create_memory_object_stream` for its bounded send/receive
  semantics) — but the subprocess spawn is plain stdlib. The fallback
  named in the prior text (swap `FFmpegWorker.start()` to
  `anyio.open_process`) is preserved as a **revisit trigger**: if a
  concrete ffmpeg-monitoring corner case appears in integration
  testing (Phase 12 or later), the swap stays local thanks to the
  `ProcessSpawner` Protocol introduced in `app/fanout/process.py`
  (Phase 5).
- If we ever need to scale beyond one node, the choice of SQLite
  (ADR-0007) will bind before this ADR does.
