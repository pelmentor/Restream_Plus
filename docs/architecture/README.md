# Architecture documents

This folder is the authoritative source for *why* Restream_Plus is built the
way it is. Code follows decisions. If code and an ADR ever disagree, the ADR
wins until the ADR is explicitly superseded.

## Read first

- [system-overview.md](system-overview.md) — the C4 container view, domain
  language, data flow, failure modes, quality attributes.

## Architecture Decision Records

Each ADR uses the standard Michael Nygard template:
Status → Context → Decision → Consequences. Trade-offs are named explicitly
per the software-architect rule "no architecture astronautics — name what
you're giving up, not just what you're gaining".

| #    | Title                                                                | Status   |
| ---- | -------------------------------------------------------------------- | -------- |
| 0001 | [Backend stack: Python 3.12 + FastAPI](ADR-0001-backend-stack.md)    | Accepted |
| 0002 | [Frontend stack: React 19 + Vite + Tailwind v4](ADR-0002-frontend-stack.md) | Accepted |
| 0003 | [RTMP ingest + fan-out: nginx-rtmp + Worker supervisor](ADR-0003-rtmp-ingest-and-fanout.md) | Accepted (amended 2026-05-15) |
| 0004 | [Docker shape: single image with s6-overlay](ADR-0004-docker-shape.md) | Accepted (amended 2026-05-15) |
| 0005 | [Auth model: single admin, Argon2id, session cookie + API token](ADR-0005-auth-model.md) | Accepted (amended 2026-05-15) |
| 0006 | [Secret encryption at rest: AES-256-GCM from master passphrase](ADR-0006-secret-encryption.md) | Accepted (amended 2026-05-15) |
| 0007 | [Persistence: SQLite (WAL), single-shot schema](ADR-0007-persistence.md) | Accepted |
| 0008 | [Worker abstraction & log-redaction sink](ADR-0008-worker-abstraction.md) | Accepted |
| 0009 | [Credential model and lifecycle](ADR-0009-credential-model.md) | Accepted |
| 0010 | [Headless restart & master-passphrase availability](ADR-0010-headless-restart.md) | Accepted |
| 0011 | [Health, liveness, and self-checks](ADR-0011-health-liveness.md) | Accepted |

## Design review log

- [2026-05-15](design-review-2026-05-15.md) — four expert subagents
  (Backend Architect, Software Architect, ArchitectUX, UI Designer)
  reviewed the initial design. Outcome: 5 new ADRs (0007–0011), 4
  amendments to existing ADRs, substantial expansion of UI/UX docs.
  Every finding is mapped to a decision and a touched file.

## Per-phase design memos

Locked decisions for each implementation phase. Each memo is the
synthesis of 1–3 parallel agency-agent passes (Rule №3) and is binding
for that phase's code. Read the relevant memo before extending that
phase's surface.

- [phase-6-design-memo.md](phase-6-design-memo.md) — Backend HTTP
  (REST + WebSocket + middleware + lifespan + supervisor wiring).
- [phase-7-design-memo.md](phase-7-design-memo.md) — Frontend
  foundation (theme + shell + router + login + locked/unlock).
- [phase-8-design-memo.md](phase-8-design-memo.md) — Frontend
  Dashboard (hero + tiles + slide-out + WS-cache patcher + RecentEvents
  + VK paste mid-flow + first-run hint).
- [phase-9-design-memo.md](phase-9-design-memo.md) — Frontend
  Settings (SettingsShell + 9 tabs + 6 design-system components +
  AuthRepromptHost + 6 new backend endpoints; rotate-passphrase
  atomic re-wrap + sessions list/revoke).
- [phase-10-design-memo.md](phase-10-design-memo.md) — Container
  (Dockerfile multi-stage + nginx.conf + entrypoint.sh + s6 service
  tree + healthcheck.sh; 60 locked invariants I-A1..I-Q2 covering
  build topology, SIGTERM choreography, log redaction, fd-3
  notification-fd contract, uid 10001, CAP_NET_BIND_SERVICE, and the
  app/main.py + app/config.py tweaks for s6 readiness + production
  healthz_check_nginx default).

## Rules of engagement for adding ADRs

- One decision per ADR. If you find yourself writing "and also…", split it.
- Status moves forward only: Proposed → Accepted → Deprecated → Superseded
  by ADR-XXX. Never edit an accepted ADR's Decision section silently;
  supersede it.
- Decisions are *reversible* when possible. Prefer cheap-to-change over
  "optimal".
- Cite trade-offs and rejected alternatives. Future readers (including me)
  need to know what was considered.
