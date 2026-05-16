# Session Handoff — Restream_Plus

**Purpose:** anything in this file is the state I would need to pick up
this project cold, after a context compaction or a fresh conversation.
Read this first; everything else is reachable from here.

**Last updated:** 2026-05-17

**GitHub:** https://github.com/pelmentor/Restream_Plus
(initial commit `a100f2a` pushed 2026-05-17 covers Phases 0–10;
local `main` tracks `origin/main`. Author identity is set
**repo-locally** to `pelmentor <cssnik2013@gmail.com>` — `.git/config`,
not global.)

---

## TL;DR

Self-hosted RTMP restreamer (OBS → server → Twitch / YouTube Live / Kick /
VK Video Live) with a web control panel, packaged as one Docker image,
published to GHCR. **Source pushed to
https://github.com/pelmentor/Restream_Plus** (2026-05-17 initial
commit covers Phases 0–10). **Design complete. Phases 1 (config + crypto),
2 (persistence), 3 (auth), 4 (domain), 5 (supervisor + ffmpeg workers
+ redaction + event bus), 6 (Backend HTTP — REST + WebSocket + health
+ locked-mode middleware + lifespan + supervisor wiring), 7 (Frontend
foundation), 8 (Frontend Dashboard — runnable hero / tiles / slide-out
/ WS-cache patcher / RecentEvents / first-run hint / VK paste mid-flow),
9 (Frontend Settings — entire surface: 9 tabs + 6 design-system
components + AuthRepromptHost + 6 new backend endpoints including
rotate-passphrase with atomic credential re-wrap), and 10 (Container —
4-stage Dockerfile + nginx.conf + entrypoint + healthcheck + s6
service tree with init-data oneshot + nginx + control-plane + s6-log
companion) all implemented, reviewed, audited, and green** — 636
backend tests (pytest; unchanged from Phase 9 — Phase 10 was pure
infrastructure), ruff + ruff format + mypy --strict clean; frontend
typecheck + lint + 9/9 vitest + build all clean at **214 KB gzipped
vs 256 KB budget**. **Docker is NOT installed on the dev host so
`docker build` smoke is deferred to Phase 11 CI** — the first venue
that will actually build the image. Next code phase is Phase 11
(CI: GHA workflow for build + GHCR publish) per `docs/CODE_PLAN.md`.

When resuming: read §"Resume checklist" below, then pick up at
Phase 11 in `docs/CODE_PLAN.md`.

---

## Repository / git state (2026-05-17)

- **Remote:** `origin → https://github.com/pelmentor/Restream_Plus.git`
- **Default branch:** `main` (created with `git init -b main`)
- **Initial commit:** `a100f2a` — 261 files, Phases 0–10.
- **Author identity:** `pelmentor <cssnik2013@gmail.com>` — set
  **repo-locally** in `.git/config` (NOT global). Never overwrite
  the global git config; if a teammate clones, they set their own.
- **`.gitattributes`** pins shell scripts + s6 service files +
  `Dockerfile` + `nginx.conf` to `eol=lf`. Critical: Windows checkouts
  without this give `^M: bad interpreter` errors inside the Linux
  container. Don't remove it without auditing every shell/s6 file.
- **`.gitignore`** excludes `.venv/`, `node_modules/`, `*.db`,
  `*.tsbuildinfo`, `.env*`, `*.pem`/`*.key`/`*.crt`, `.kdf_salt`,
  `.first-boot-password`, all tool caches (`.{pytest,mypy,ruff,
  hypothesis}_cache/`), `web/dist/`, build artifacts.
- **Hygiene verified** at first commit: 0 matches for any of the above
  in the staged set. No secrets, no caches, no binaries.

When pushing future commits: regular `git push` is fine. Don't
`--force` push to `main` unless explicitly asked. Don't skip hooks
(no `--no-verify`) — there aren't any installed today, but the rule
holds.

---

## Project rules (NON-NEGOTIABLE)

These come from the user, are persisted in `~/.claude/projects/.../memory/`,
and apply to every decision I make in this repo.

1. **Rule №1 — No quick-and-dirty fixes.** Proper crutchless solutions
   only, even if it takes weeks. The user does not care about delivery
   speed for this project.
2. **Rule №2 — No migration baggage.** Greenfield design. No
   backwards-compat shims. No "v1/v2" branches. Edit decisions in
   place; supersede only when the direction itself changes.
3. **Rule №3 — Don't ask the user; spawn agents instead.** For design
   choices, tech choices, and "I'm uncertain" situations, spawn 1–4
   subagents with the agency-agent persona prompts and synthesize
   their answers. Refined 2026-05-15 to cover *all* design ideation,
   not only unknowns.
4. **Rule №4 — Post-coding agent review.** After each meaningful
   coding chunk, spawn `feature-dev:code-reviewer` (and
   security-reviewer for auth/crypto/network changes) and apply their
   findings BEFORE declaring the work complete.
5. **Rule №5 — Post-shipping audit.** AFTER shipping (docs updated,
   declared "Phase N done"), audit the delivered code: do the claimed
   reviewer fixes / invariants / tests actually live in the diff?
   Catches drift between what I said landed and what landed. Added
   2026-05-16 after Phase 4 ship; immediately exercised on Phase 4
   itself — clean.

Memory entries live at:
`C:\Users\Alexgrv\.claude\projects\d--Projects-Programming-Restream-Plus\memory\`

---

## Status

| Phase                                       | Status                  | Notes                                                                                                                            |
| ------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 0. Foundation (prompts, memory, rules)      | ✅ Complete             | 4 agency-agent prompts cached; 4 rules in memory + MEMORY.md index.                                                              |
| 1. Initial design (ADRs 0001–0006 + docs + phase-design-memos) | ✅ Complete | Plus system-overview.md, platforms-reference.md (verified URLs), ux-flows.md, design-system.md, phase-6 + phase-7 design memos. |
| 2. Project scaffold                         | ✅ Complete             | `pyproject.toml`, `web/package.json`, tsconfigs, vite.config, eslint, .gitignore, .dockerignore, README, LICENSE.                |
| 3. Expert review (4 agents in parallel)     | ✅ Complete             | Backend / Software / UX / UI architect personas. ~50 findings, all logged in `docs/architecture/design-review-2026-05-15.md`.    |
| 4. New ADRs 0007–0011                       | ✅ Complete             | Persistence, Worker abstraction, Credential model, Headless restart, Health/liveness.                                            |
| 5. Amend ADRs 0001/0003/0004/0005/0006      | ✅ Complete             | Edited in place per Rule №2. README index updated.                                                                                |
| 6. Update system-overview / design-system / ux-flows | ✅ Complete    | All review findings landed.                                                                                                       |
| 7a. Backend Phase 1 — config + crypto (KDF, AEAD, passwords, logging redaction) | ✅ Complete 2026-05-16 | 89 tests; ruff + mypy --strict clean; both reviewers' findings applied. See §"Phase 1 — what landed".                |
| 7b. Backend Phase 2 — persistence (SQLite WAL, schema.sql, repositories)        | ✅ Complete 2026-05-16 | 128 tests (39 new); ruff + mypy --strict clean; code-reviewer findings applied. See §"Phase 2 — what landed".        |
| 7c. Backend Phase 3 — auth (sessions, API tokens, rate-limit, deps, key material) | ✅ Complete 2026-05-16 | 262 tests (134 new); ruff + ruff format + mypy --strict clean; code-reviewer + security-reviewer findings applied. See §"Phase 3 — what landed". |
| 7d. Backend Phase 4 — domain (Target / Credential / RunState / WorkerState / health) | ✅ Complete 2026-05-16 | 424 tests (162 new, incl. property-based circuit-breaker); ruff + ruff format + mypy --strict clean; code-reviewer findings applied. See §"Phase 4 — what landed". |
| 7e. Backend Phase 5 — supervisor (FFmpegWorker / progress / redaction / event bus) | ✅ Complete 2026-05-16 | 525 tests (101 new); ruff + ruff format + mypy --strict clean; general + security reviewers ran in parallel — 3 highs + 5 mediums + 4 lows applied; ADR-0006 amended; Rule №5 audit clean. See §"Phase 5 — what landed". |
| 7f. Backend Phase 6 — HTTP (REST + WebSocket + middleware + lifespan) | ✅ Complete 2026-05-16 | 610 tests (85 new); ruff + ruff format + mypy --strict clean; code-reviewer (4 highs + 3 mediums) + security-reviewer (1 critical + 4 highs + 2 mediums + 2 lows) ran in parallel; all four Phase 3 deferred gaps closed; Rule №5 audit clean. See §"Phase 6 — what landed". |
| 7g. Frontend Phase 7 — foundation (theme + shell + router + login + dashboard placeholder) | ✅ Complete 2026-05-16 | Backend: +1 endpoint `GET /api/auth/me` + 4 tests (614 total green); ruff + ruff format + mypy --strict clean. Frontend: 33 new files (web/src/); typecheck + lint (eslint v9 flat config + jsx-no-literals) + build all clean; 139 KB gzipped vs 250 KB budget. code-reviewer 1 high + 3 mediums + 3 lows — all applied (H-1 ARIA menu role, M-1 mutation re-broadcast guard, M-2 useReducedMotion wiring, M-3 missing bg-overlay token, L-3 deprecated --ext flag). Rule №5 audit clean. See §"Phase 7 — what landed". |
| 7h. Frontend Phase 8 — Dashboard (hero card + tiles + slide-out + WS subscription)         | ✅ Complete 2026-05-16 | Backend: +1 field `RunStateView.run_state_changed_at` + supervisor tracking + 4 tests (616 total green). Frontend: 19 new files + 3 modified; typecheck + lint + build clean; **168 KB gzipped vs 256 KB budget**; 9/9 Vitest pure-function reducer tests. code-reviewer 2 highs + 3 mediums + 1 low — all applied (H-1 aria-live on button, H-2 useTargets reactivity hole, M-1 password autofill suppress, M-3 double hsl wrap, M-4 slide-out focus return, L-2 RecentEventsMenu Escape). Rule №5 audit clean. See §"Phase 8 — what landed". |
| 7i. Frontend Phase 9 — Settings (all tabs + 6 design-system components + 6 new backend endpoints) | ✅ Complete 2026-05-16 | Backend: 6 new endpoints (reveal-credential / reveal-ingest-key / rotate-passphrase / GET+DELETE security/sessions / GET /about) + new `REVEAL_INGEST_KEY` scope + 5 new error codes + `KeyMaterial.verify_passphrase`/`rotate_in_place` + repo additions (list_all_for_rewrap/rewrap, list_active_for_user/delete_all, delete_all, list_app_starts) + lifespan tracking of `started_at` + `app_started` audit + 24-h `.kdf_salt.previous` cleanup task; +20 endpoint tests (636 total green) covering rotate-passphrase atomicity injection. Frontend: 36 new files (lib + hooks + components + 9 tabs); typecheck + lint + 9/9 Vitest + build all clean at **214 KB gzipped vs 256 KB budget**. code-reviewer + security-reviewer 1 high + 5 mediums — all applied (H-1 SecretStr for passphrase fields, M-1 rate-limit countdown reset, M-2 sessions assertion fix, M-3 ChangePassword err.code match, M-4 cleanup-loop suppress scope, M-5 unmount plaintext cleanup). Rule №5 audit clean 24/24. See §"Phase 9 — what landed". |
| 10. Container (Dockerfile, nginx-rtmp build, s6 services, entrypoint)                   | ✅ Complete 2026-05-16 | 4-stage Dockerfile (frontend-builder + nginx-builder + backend-builder + runtime). docker/{nginx.conf, entrypoint.sh, healthcheck.sh, nginx-rtmp.sha, requirements.lock} + s6 tree {init-data oneshot + nginx longrun (with port-poll readiness wrapper) + control-plane longrun + control-plane/log s6-log companion + user/contents.d bundle}. app/main.py adds `_notify_s6_ready()` (write+close fd 3) + explicit `workers=1`. app/config.py flips `healthz_check_nginx` default to True (production-correct); 4 conftests + 1 test fixture opt out. 60 invariants I-A1..I-Q2 in phase-10-design-memo.md (synthesis of Software Architect + Backend Architect agency-agent passes). 636 backend tests still green; ruff + ruff format + mypy --strict clean. code-reviewer (2 criticals + 2 importants + 1 medium) + security-reviewer (2 highs + 4 mediums + 5 lows) — all applied except H2 (pip --require-hashes deferred to Phase 11 with the dep-bump tooling). Rule №5 audit CLEAN (13/13 fixes verified). `docker build` runtime smoke deferred to Phase 11 CI (Docker not installed on dev host). See §"Phase 10 — what landed". |
| 11. CI (GHA, multi-arch GHCR publish)                                                   | ⏸ Not started | Inherits Phase 10 invariants: SHA-256 build-arg population (DA-6), pip --require-hashes lockfile (DA-5), Renovate/Dependabot bumps, multi-arch build (qemu vs ubuntu-24.04-arm runner tradeoff per I-L4). |
| 12. Ops docs (deployment, backup/restore, release checklist, maintenance)               | ⏸ Not started | Must cover: docker stop -t 30 (I-D4); /data ownership chown-on-start (§F); nginx-error.log treated as secret (§I); rootless-podman --cap-add NET_BIND_SERVICE (I-G5); bind :1935 to loopback or LAN (never public); /readyz vs /livez probe semantics (§H). |

---

## Architecture in one paragraph

OBS pushes RTMP to nginx-rtmp inside the container. nginx fires the
`on_publish` webhook to the FastAPI control plane to authenticate. The
control plane supervises one or more **Worker** instances per **Target**
(typed protocol; v1 has `FFmpegWorker`); each Worker pulls from
`rtmp://127.0.0.1/live/<ingest-key>` and pushes passthrough (`-c copy`)
to a remote platform. Encoding is OBS's job, running on the user's GPU
(e.g., RTX 5060 NVENC) — the relay does not transcode. The control
plane runs two asyncio `TaskGroup`s (supervisor + API) with a bounded
queue between them so a slow consumer cannot starve the supervisor.
SQLite (WAL) persists users, sessions, targets, **credentials** (a
separate entity with `lifetime` policy — VK is `per_session`, others
`persistent`), and an audit log. Stream keys are encrypted at rest with
AES-256-GCM under a key derived from `RESTREAM_MASTER_PASSPHRASE` via
Argon2id + HKDF. The React 19 + Vite + Tailwind v4 SPA shows live
status over WebSocket. Three health endpoints (`/livez`, `/readyz`,
`/healthz`) expose process / DB / nginx / supervisor liveness. One
Docker image with s6-overlay supervises nginx and the control plane.
GHA publishes to GHCR.

---

## Resume checklist (read in this order)

1. **This file** (`docs/SESSION_HANDOFF.md`) — orientation.
2. **`docs/CODE_PLAN.md`** — the phased implementation plan with file
   paths and acceptance criteria. **Pick up at whatever phase is
   active** (next is Phase 11 — CI: GHA workflow for build + GHCR publish).
3. **`docs/architecture/README.md`** — index of 11 ADRs + the design
   review log.
4. **`docs/architecture/system-overview.md`** — domain language and the
   container-level architecture diagram. Skim if you've never seen it;
   re-read if it's been more than a few hours.
5. **`docs/architecture/design-review-2026-05-15.md`** — every
   architectural finding from the 4-expert review and how it was
   resolved. Use this when something in the ADRs seems surprising —
   the rationale is here.
6. **Memory** at `~/.claude/projects/d--Projects-Programming-Restream-Plus/memory/`:
   - `MEMORY.md` (index)
   - The 5 rule files (especially `rule-no-quick-fixes.md`,
     `rule-no-questions-spawn-agents.md`, and `rule-post-shipping-audit.md`
     for the post-ship verification step).
7. **The four agency-agent prompts** at `docs/prompts/` — these
   define how to brief future expert-review subagents. Don't expand
   on them; just re-use them when spawning the agents per Rule №3 / №4.

After this checklist, you should know what state the project is in and
what to do next without asking the user.

---

## Phase 1 — what landed (2026-05-16)

The boot-time crypto + logging layer that every downstream phase imports.

### Modules created

```
app/
├── __init__.py                # re-exports __version__, BUILD_SHA
├── version.py                 # __version__ = "0.1.0"; BUILD_SHA from env
├── config.py                  # AppSettings (pydantic-settings)
│                              #   - passphrase_source: "env" (default) | "paste"
│                              #   - ADR-0010 invariants enforced (no silent fallback)
│                              #   - SecretStr for all credential-like fields
├── crypto/
│   ├── __init__.py            # public re-exports
│   ├── kdf.py                 # Argon2id(passphrase, salt) → root_key (32B)
│   │                          # HKDF-SHA256(root_key, info) → subkey (32B)
│   │                          # Two infos pinned: stream-key-wrap-v1, api-token-mac-v1
│   ├── aead.py                # AES-256-GCM wrapper. Internal nonce generation
│   │                          #   (prevents reuse), AAD = b"credential:stream_key:v1" + salt
│   │                          #   Raises AEADDecryptionError (wraps InvalidTag) on any tamper.
│   │                          #   Refuses ciphertext shorter than GCM tag (16B).
│   └── passwords.py           # Admin password Argon2id (PasswordHasher);
│                              #   verify_password returns PasswordVerificationResult
│                              #   (ok, needs_rehash). Empty password short-circuits
│                              #   before Argon2 (DoS amplification mitigation).
└── logging_setup.py           # structlog setup + CredentialRegistry (thread-safe,
                               #   global) + redaction processor. Redaction sorts
                               #   secrets longest-first so nested substrings don't leak.
                               #   format_exc_info runs before redaction so the formatted
                               #   traceback string is in scope of the scanner.
```

Tests at `tests/{test_config.py, test_logging_setup.py, crypto/test_*.py}`:
**89 tests, ruff + ruff format + mypy --strict all clean.** Property-based
tests cover AEAD roundtrip+tamper (200 examples each) and redaction
(100 examples).

### Reviewer findings applied

Both `feature-dev:code-reviewer` and a security-focused review of the
same scope ran per Rule №4. Resolutions in code:

- Redaction sorts secrets longest-first so a registered short secret
  cannot fragment a registered longer one (substring leak fix).
- AEAD tamper property test now uses a wide `flip_byte` range so the
  GCM tag region is reachable for ciphertexts >256 bytes.
- New test asserts exceptions whose messages contain a registered
  secret are redacted (`format_exc_info` → redaction interaction is
  the only path where library exceptions could leak a credential).
- `verify_password("")` returns ok=False without invoking Argon2.
  Timing-budget test asserts the early-return path stays <10ms.
- `decrypt()` rejects ciphertexts shorter than the GCM tag with a
  `ValueError` (input bug) rather than `AEADDecryptionError` (auth
  failure) — operational clarity for callers reading from the DB.
- `captured_stream` test fixture no longer calls `configure_logging()`
  (that mutated global state); it builds a minimal processor chain
  locally and tears it down via `structlog.reset_defaults()`.
- Default `data_dir == Path("/data")` now has an explicit test (ADR-0007
  contract).

The one security finding NOT applied: a marginal inline-the-local
suggestion in `config.py`. ADR-0006 already names the memory-dump
threat model as out of scope for runtime mitigation; the change would
not narrow that.

### Concrete invariants now in the codebase (worth knowing)

- **HKDF info strings are pinned by test.** Changing `stream-key-wrap-v1`
  or `api-token-mac-v1` is a key-rotation event, not a refactor.
- **AAD discriminator is pinned by test.** Same reasoning —
  `b"credential:stream_key:v1"` is part of the on-disk format.
- **Argon2 parameters are pinned by test.** KDF: 128 MiB / 3 / 2.
  Admin password hasher: 64 MiB / 3 / 2.
- **Redaction `MIN_REDACTABLE_SECRET_LENGTH = 12`.** Registering
  anything shorter is rejected at the boundary. Stream keys, API
  tokens, and Argon2 PHC strings are all well above this.
- **`AEADDecryptionError` does NOT distinguish failure modes.** Wrong
  key, tampered ciphertext, tampered nonce, wrong AAD — all produce
  the same message. Callers must not branch on which.
- **Tests run fast.** 89 tests in ~1.4s. If a future test crosses 5s,
  something is wrong (likely an Argon2 call where there shouldn't be one
  or a thread/barrier deadlock — both have been hit and fixed; the
  thread test now has explicit per-thread `barrier.wait(timeout=5)` and
  `t.join(timeout=10)` so a regression fails fast rather than hanging).

---

## Phase 2 — what landed (2026-05-16)

The persistence layer that everything from Phase 3 onward builds on.

### Modules created

```
app/
├── db/
│   ├── __init__.py            # public re-exports
│   ├── schema.sql             # canonical schema (single source of truth, ADR-0007)
│   │                          # 9 STRICT tables: users, sessions, api_tokens,
│   │                          # targets, credentials, audit_log,
│   │                          # sessions_history, settings, system
│   ├── types.py               # UTCEpochSeconds TypeDecorator (rejects naive datetimes)
│   ├── models.py              # SQLAlchemy DeclarativeBase + ORM mappings
│   ├── engine.py              # async engine + PRAGMAS dict + connect hook
│   ├── session.py             # async_sessionmaker factory
│   └── schema_init.py         # initialize_schema(engine, settings):
│                              #   - sync sqlite3 bootstrap via executescript
│                              #   - seed singleton settings + admin row
│                              #   - reset-admin-password flow (ADR-0005)
│                              #   - crash-recovery for orphan run-sessions
│                              #   - SchemaVersionMismatchError on version drift
└── repositories/
    ├── __init__.py            # public re-exports
    ├── users.py               # UserDTO + UsersRepository
    ├── sessions.py            # HttpSessionDTO + HttpSessionsRepository
    ├── api_tokens.py          # ApiTokenDTO (omits token_hash) + ApiTokensRepository
    ├── targets.py             # TargetDTO (decodes settings_json) + TargetsRepository
    ├── credentials.py         # CredentialDTO + CredentialsRepository (upsert/get/delete)
    ├── settings_repo.py       # SettingsDTO + SettingsRepository (rotate_ingest_key is atomic SQL)
    ├── audit_log.py           # AuditLogEntryDTO + AuditLogRepository
    │                          #   - constructor takes sessionmaker (own short-lived tx)
    │                          #   - append() runs PRAGMA wal_checkpoint(TRUNCATE)
    │                          #     and warns if SQLite reports `busy=1`
    └── sessions_history.py    # RunSessionDTO + SessionsHistoryRepository
```

Tests at `tests/db/{test_engine, test_schema_init, test_repositories}.py`:
**128 tests in ~3.8s; ruff + ruff format + mypy --strict all clean.**

### Reviewer findings applied

`feature-dev:code-reviewer` ran per Rule №4 (Phase 2 is not in the
security-review list per `CODE_PLAN.md` §"Per-phase ritual"). Six
findings, all applied:

- **WAL checkpoint result is verified.** `append()` reads the
  `(busy, log_pages, checkpointed)` return row and emits a structured
  warning if `busy=1`. The audit row is still durable in the WAL —
  loss only happens on power-cut between commit and a successful
  checkpoint — but a busy report surfaces a held-open reader for
  operator investigation.
- **No split-on-semicolon SQL parser.** Schema bring-up uses the
  synchronous `sqlite3` driver under `asyncio.to_thread` so we can
  call `Connection.executescript`, the canonical multi-statement
  entry point. The async engine handles only the typed ORM seed,
  reset, and recovery operations.
- **`conn: object` widening removed.** Helpers now annotate
  `conn: AsyncConnection` via the `from __future__ import annotations`
  forward-ref pattern; the runtime `assert isinstance` checks are gone.
- **Crash-recovery audit data is labelled honestly.** The audit row
  records `started_at` (not `estimated_end_at`) with a `note`
  explaining that status snapshots are WebSocket-only, so the true
  end time is unknown.
- **`rotate_ingest_key` is atomic.** The UPDATE uses a column
  self-reference (`ingest_key_previous = ingest_key_current`) so
  there's no read-then-write window. Two concurrent rotations (which
  the single-writer model already makes unlikely) cannot break the
  previous-key chain.
- **Inline `from sqlalchemy import select` imports removed** from
  four tests; all imports are at module top.

### Concrete invariants now in the codebase (worth knowing)

- **`schema.sql` is the source of truth.** `models.py` matches it by
  hand. A drift test (`test_orm_models_match_schema_sql`) compares
  table and column names — adding a column in one place but not the
  other fails the test loudly.
- **Pragmas live in `app/db/engine.py:PRAGMAS`.** Test
  `test_all_documented_pragmas_take_effect` verifies each pragma reads
  back as the ADR-0007 value. `journal_mode=WAL` is also confirmed
  by the presence of `-wal`/`-shm` sidecar files after the first write.
- **Schema version is `1`.** `SCHEMA_VERSION` constant in
  `schema_init.py`. Bumping it requires the operator to export +
  reimport (Rule №2 — no migrations chain). Version-mismatch error
  message names the procedure and the involved versions.
- **All timestamps are stored as INTEGER epoch seconds (UTC).**
  `UTCEpochSeconds` TypeDecorator rejects naive datetimes at bind
  time so an accidental `datetime.utcnow()` can't land in the DB.
- **`ApiTokenDTO` does not expose `token_hash`.** Listing tokens
  returns only metadata.
- **`AuditLogRepository` takes a sessionmaker, not a session.** Each
  `append` runs in its own short-lived transaction so the caller's
  business-work transaction (if any) is not held open by the
  checkpoint. The WAL checkpoint is verified, not silently ignored.
- **Crash recovery is on every boot.** Any `sessions_history` row
  with `ended_at IS NULL` is patched (end_reason='control_plane_crash')
  and an audit row is appended. Do NOT auto-resume — the user
  explicitly clicks START again per ADR-0007.

---

## Phase 3 — what landed (2026-05-16)

The authentication layer: cookie sessions, API tokens, rate limiting,
short-lived reprompt grants, and the `KeyMaterial` 3-state machine
that unlocks the encryption KEK. ADR-0005 and ADR-0006 were amended
during this phase; ADR-0010 was amended to retract the
"login-on-locked" conflation.

### Design memo first (Rule №3)

Before writing a line of code, a Backend Architect persona answered
seven open design questions and produced a memo:

- **Q1 (cookie HMAC keying):** Derive a new HKDF subkey
  `session-token-mac-v1` rather than reuse `api-token-mac-v1`.
  Principle of key separation; one extra `derive_subkey` call at boot
  costs nothing. ADR-0006 amended.
- **Q2 (load-bearing):** Split `/api/unlock` (passphrase → KEK) from
  `/api/auth/login` (password → session). `KeyMaterial` singleton
  with `LOCKED → UNLOCKING → READY` states. ADR-0010 amended to
  retract the previous conflation.
- **Q3 (CSRF):** SameSite=Lax + `__Host-` prefix + no GET mutations
  ⇒ no CSRF token needed.
- **Q4 (rate-limit identity):** Paired-bucket (per-IP AND per-username,
  both must be under threshold). Trusted-proxy CIDR list env var
  `RESTREAM_TRUSTED_PROXIES` + rightmost-untrusted-hop walk.
- **Q5 (reprompt store):** In-memory scope-bound 60s grants. Single
  uvicorn worker per ADR-0001.
- **Q6 (login response):** 200 with `{"user": {"username",
  "last_login_at"}}` (was 204). ADR-0005 amended.
- **Q7 (bearer transport):** `Authorization: Bearer` only — no
  query-param auth.

### Modules created

```
app/
├── auth/
│   ├── __init__.py
│   ├── key_material.py       # 3-state machine (LOCKED → UNLOCKING → READY).
│   │                         # Argon2id + 3 HKDF subkeys in a worker thread.
│   │                         # bytearray-zero of the encoded passphrase.
│   │                         # Idempotent unlock under READY (no re-derive).
│   ├── sessions.py           # SessionAuthService: login + verify + revoke.
│   │                         # Cookie value: secrets.token_urlsafe(32).
│   │                         # token_hash = HMAC-SHA256(session_token_mac_key, cookie).
│   │                         # __Host-rp_session, HttpOnly, Secure, SameSite=Lax.
│   │                         # *** Rate-limit check + record_failure embedded
│   │                         #     in login() per Phase 3 security review CRIT.
│   │                         # Lazy _dummy_password_hash() via functools.cache.
│   │                         # Constant-time floor (default 0.25 s) on failure path.
│   │                         # `needs_rehash` surfaced on LoginOutcome; rehash
│   │                         # is NOT applied inline (timing-channel fix).
│   ├── api_tokens.py         # ApiTokenService: create / verify / revoke.
│   │                         # `rpat_` prefix; secrets.token_urlsafe(40) random.
│   │                         # HMAC-SHA256(api_token_mac_key, plaintext).
│   │                         # `Authorization: Bearer …` only (no query param).
│   ├── rate_limit.py         # LoginRateLimiter: paired sliding-window (ip+username).
│   │                         # username goes through normalize_username before bucketing.
│   │                         # extract_client_ip(): rightmost-untrusted-hop walk
│   │                         # with parse-first IPv6 disambiguation.
│   ├── reprompts.py          # RepromptStore: 60s scope-bound grant tokens.
│   │                         # Logs SHA-256 grant-ID fingerprint, not plaintext.
│   │                         # Single-use; burn on wrong-user/scope.
│   ├── usernames.py          # normalize_username: NFKC + casefold + strip.
│   └── deps.py               # FastAPI Depends helpers.
│                             # AuthState dataclass (lifespan-built bundle).
│                             # get_db_session commits-on-success.
│                             # require_unlocked_keys → 503 service_(un)locked.
│                             # require_reprompt_grant(scope) → async dep factory.
```

Plus amendments to `app/crypto/kdf.py` (`HKDF_INFO_SESSION_TOKEN_MAC`)
and `app/config.py` (`trusted_proxies: tuple[IpNetwork, ...]` with
`NoDecode` annotation).

### Reviewer findings — two reviewers ran in parallel (Rule №4)

Both `feature-dev:code-reviewer` (general correctness) and a
security-focused reviewer ran against the diff. Their findings,
applied in this order:

**Critical / applied:**

- *Rate-limiter unwired.* `LoginRateLimiter` was instantiated in
  `AuthState` but never called by any login path; ADR-0005's
  brute-force defence was effectively absent. **Fix:** the limiter is
  now a constructor-required dependency of `SessionAuthService`, with
  `check()` before Argon2 and `record_failure()` on
  `InvalidCredentialsError`. Constructing a service without one is
  structurally impossible.

**High / applied:**

- *`_strip_port` mis-handled unbracketed IPv6+port.* The old
  `count(":") == 1` rule could bucket different client ports to
  different keys for IPv6, defeating per-IP limiting. **Fix:**
  `ipaddress.ip_address`-parse first; only fall back to port-stripping
  on a non-parseable input. Documented test for the ambiguity case.
- *Username normalization gap.* `str.lower()` ASCII-folds only;
  Unicode lookalikes (fullwidth `ＡＤＭＩＮ`, padded `'  admin  '`,
  Turkish `ADMİN`) bucket as distinct identities. **Fix:** new
  `app/auth/usernames.py::normalize_username` applies NFKC +
  `casefold()` + `strip()`. The Turkish dotless-i `ı` is intentionally
  NOT unified with Latin `i` (legit distinct Unicode letter); a test
  pins this so a future over-broadened fix can't accidentally make
  `admın` log in as `admin`.
- *Rehash-on-login timing channel.* Inline `hash_password` after a
  successful verify added ~100 ms variability to the success path.
  **Fix:** the inline rehash is removed; `needs_rehash` is surfaced
  on `LoginOutcome` for an out-of-band consumer (Phase 6
  `BackgroundTasks` or a maintenance job).

**Medium / applied:**

- *Dummy hash at module import.* `_DUMMY_PASSWORD_HASH` ran Argon2
  on first `import app.auth.sessions`, adding ~100 ms to test
  collection. **Fix:** wrapped in `functools.cache` so the cost is
  paid on first login, not on import.
- *Dead helper `constant_time_cookie_compare`* — never called, hinted
  at protection it didn't provide. **Fix:** deleted.
- *Grant ID prefix logged plaintext.* Six chars of URL-safe-base64 is
  36 bits — usable for log-correlation attacks. **Fix:** log a
  truncated SHA-256 fingerprint instead.
- *HKDF info pin test missing for new info.* **Fix:** test pins
  `HKDF_INFO_SESSION_TOKEN_MAC == b"session-token-mac-v1"` and
  asserts the three infos are pairwise distinct.
- *Passphrase byte residue.* `passphrase.encode("utf-8")` leaves an
  immutable `bytes` copy in heap until GC. **Fix:** wrap in a
  `bytearray` and zero it in a `finally:` clause before scope exit.
  Honest defence-in-depth; ADR-0006 still names memory-dump out of
  scope.
- *`require_reprompt_grant._dep` was sync `def`.* FastAPI would run
  it in a thread pool, violating `RepromptStore`'s documented
  single-event-loop concurrency model. **Fix:** `async def`.

**Low / applied:**

- *BEARER regex tolerated `\s+`.* Tightened to literal `+` to reject
  folded headers and tabs more crisply.

### Deferred to Phase 6 (gaps documented here so they cannot be lost)

- **Router-level locked-mode guard.** Today, `require_unlocked_keys`
  is transitively reached through `SessionServiceDep` /
  `ApiTokenServiceDep`. A future handler that consumes `SessionDep`
  without going through those would not 503 in locked mode. Phase 6
  should add either a router-level `Depends(require_unlocked_keys)`
  on the main auth router or a middleware that 503s any non-
  `/api/unlock` request when `KeyMaterialState != READY`.
- **Hardcoded `"admin"` username** in
  `get_current_user_via_bearer`. Today the schema does not join
  `api_tokens` to `users` via FK, so the bearer's owner is identified
  by the single-admin invariant. Phase 6 (or earlier, when schema is
  reopened) should add `api_tokens.user_id REFERENCES users(id)`.
- **Reprompt burn-on-failure DoS.** An attacker who somehow learns a
  grant ID can burn it before the operator's legitimate request. The
  current pop-before-validate semantic is defensible (defence against
  probing attackers who shouldn't know the ID at all), but document
  this in any UI that surfaces grant IDs.
- **First-login rehash.** Phase 6 attaches a `BackgroundTasks`
  `hash_password` + `update_password_hash` consumer to the login
  endpoint when `LoginOutcome.needs_rehash` is True.

### Concrete invariants now in the codebase (worth knowing)

- **`SessionAuthService` cannot exist without a `LoginRateLimiter`.**
  Constructor signature enforces it. Tests build a fresh limiter per
  test for isolation.
- **All authenticated requests pass through `require_unlocked_keys`**
  *as long as they reach it via `SessionServiceDep` /
  `ApiTokenServiceDep`* — Phase 6 must extend this to *all* auth
  endpoints (see deferred gap above).
- **`session-token-mac-v1` HKDF info is byte-pinned** by a test. Any
  refactor that mangles the literal is caught at PR time.
- **The three HKDF infos are pairwise distinct** by test (key
  separation invariant).
- **Username normalization is consistent between rate-limit bucketing
  and DB lookup.** Both go through `normalize_username`, so a Unicode
  lookalike that's a distinct user under casefold is also a distinct
  bucket (and matches no row in the single-admin DB).
- **`_strip_port` parses first.** A bare IPv6 like `2001:db8::1` or
  `2001:db8::1:443` is preserved verbatim; bracketed-with-port
  `[addr]:port` is the only form whose port is stripped.
- **Grant-ID logging surfaces a SHA-256 fingerprint** (12 hex chars),
  never the plaintext grant ID.
- **Tests stay fast.** 262 tests in ~11 s on a modern laptop. If a
  future test crosses 5 s on the laptop, an Argon2 call has snuck
  into a fast path; investigate.

---

## Phase 4 — what landed (2026-05-16)

The pure-Python domain layer that Phase 5 (supervisor) and Phase 6
(HTTP) both consume. No I/O, no asyncio, no subprocess. State
machines, value objects, and one DTO→domain adapter at the persistence
boundary.

### Design memo first (Rule №3)

A Backend Architect persona answered eight design questions before the
first line of code:

- **Q1 (state-machine pattern):** Enum + frozen `dict[(state, action),
  state]` + free `transition()` function for both `RunState` and
  `WorkerState`. Pure functional; serializes naturally.
- **Q2 (illegal transition exception):** `IllegalStateTransitionError`
  base + `IllegalRunStateTransitionError` / `IllegalWorkerStateTransitionError`
  subclasses. Carries enums only — redaction-trivial.
- **Q3 (circuit breaker):** Separate `CircuitBreaker` policy object;
  state machine consumes a `BreakerVerdict`. Keeps the state machine
  deterministic and the breaker testable in isolation.
- **Q4 (Target aggregate):** `WorkerSnapshot` value object decouples
  domain from Phase 5's concrete `Worker`. Supervisor pumps snapshots
  in via `Target.ingest_snapshot(snap)`.
- **Q5 (`Target.ui_state()`):** 8-value enum with explicit elif-ladder
  precedence so the rule reads top-to-bottom as the spec.
- **Q6 (health):** `TargetHealth` dataclass with `level` (3-value enum),
  `healthy_roles`, `unhealthy_roles`, `is_live` property. Distinct from
  `ui_state()` — answers "is data flowing?" vs "what does the tile show?".
- **Q7 (`target_types.py`):** Frozen `TargetTypeSpec` dataclass keyed
  by `TargetType` in a `Final[dict]`. Single source of truth for default
  URLs, backup support, credential lifetime, settings-form fields.
- **Q8 (module boundaries):** Domain imports nothing from
  `app.repositories` / `app.db` / `app.crypto` / `app.auth` / `asyncio` /
  `subprocess`. Conversion lives at `app/repositories/_converters.py`.
  Mechanically enforced by `tests/domain/test_no_outward_imports.py`
  (AST-walk every domain module).

### Modules created

```
app/
├── domain/
│   ├── __init__.py             # public re-exports of every domain symbol
│   ├── errors.py               # IllegalStateTransitionError + 2 typed subclasses
│   ├── credential_policy.py    # CredentialLifetime enum + lifetime_for()
│   ├── target_types.py         # TargetType + FormField + TargetTypeSpec
│   │                           #   + TARGET_TYPE_SPECS (the catalogue) + spec_for()
│   ├── circuit_breaker.py      # BreakerVerdict + CircuitBreakerPolicy
│   │                           #   + CircuitBreaker (sliding window + healthy reset)
│   ├── run_state.py            # RunState + RunAction + transition() + is_legal()
│   │                           #   + legal_actions() — system-wide machine
│   ├── worker_state.py         # WorkerState + WorkerAction + WorkerRole
│   │                           #   + WorkerSnapshot + transition() (verdict-routed
│   │                           #   on RECONNECT) + is_legal() + legal_actions()
│   ├── target.py               # Target aggregate + WorkerSet + TargetUiState
│   │                           #   + compute_ui_state() (8-state precedence ladder)
│   └── health.py               # TargetHealthLevel + TargetHealth + is_live property
│                               #   + aggregate_target_health()
└── repositories/
    └── _converters.py          # target_dto_to_domain() — the ONLY module
                                #   that imports from both repositories and domain
```

Tests at `tests/domain/`:
**424 tests in ~11 s; ruff + ruff format + mypy --strict all clean.**
162 new tests, including hypothesis property-based tests on the
circuit breaker (with `assume()` guard so window invariants stay
self-correcting if params change).

### Reviewer findings — `feature-dev:code-reviewer` (Rule №4)

Phase 4 is pure-Python with no auth/crypto/network surface, so per
`CODE_PLAN.md` §"Per-phase ritual" no security-reviewer was required.
General code-reviewer ran and surfaced 2 highs + 1 medium, all applied:

**High / applied:**

- *`WorkerSet.healthy_count` footgun.* The helper iterated `snapshots.
  values()` — counting roles outside `expected_worker_roles()`. If a
  config change races worker tear-down, a stale snapshot would inflate
  the count vs. `aggregate_target_health.healthy_roles`. **Fix:** removed
  the helper; documented `WorkerSet` to direct callers to
  `aggregate_target_health` for role-filtered counts.
- *Property-test falsifiability gap.* `test_at_least_max_failures_within_
  window_opens` implicitly assumed all generated failures land inside
  the 5-minute window. Safe today by manual arithmetic, but a future
  parameter widening would silently break the property. **Fix:** added
  `assume(total_span_seconds < policy.window.total_seconds())` at the
  top of the test so hypothesis self-corrects.

**Medium / applied:**

- *`transition` failure surface inconsistent.* `transition(RECONNECTING,
  RECONNECT)` without a verdict raised `ValueError` (not the typed
  `IllegalWorkerStateTransitionError`), so a caller catching the typed
  exception would miss it. **Fix:** unified — every refusal now raises
  `IllegalWorkerStateTransitionError`. Test renamed and updated.

**Defended (not applied, with rationale):**

- DEGRADED briefly during YouTube-with-backup startup is correct
  domain behavior; UI debounce is a Phase 6/8 concern.
- `_credentials_satisfied` collapsing to `t.has_active_credential` for
  every type is correct: VK's PER_SESSION lifetime means presence of
  the row IS the gate.
- `target_dto_to_domain` dropping settings keys other than
  `backup_enabled` is correct: `target.url` is the authoritative
  runtime URL; other keys are form-layer metadata.

### Concrete invariants now in the codebase (worth knowing)

- **State machines are pure.** `transition(state, action)` is referentially
  transparent. The reconnect path takes a `BreakerVerdict` so wall-clock
  time stays out of the state machine entirely.
- **The transition tables are the source of truth.** Every `(state,
  action)` not in `_TRANSITIONS` raises the typed exception. There is
  no implicit fall-through.
- **`legal_actions(state)` is a complete predicate** for `is_legal`.
  RECONNECT is included for `RECONNECTING` because the transition is
  legal; the verdict is part of the action's payload, not a separate
  legality question. The unified failure surface ensures a missing
  verdict still raises the typed exception.
- **`WorkerSnapshot` is the only data the domain accepts about a worker.**
  Phase 5's `FFmpegWorker` projects into this shape on every event;
  the domain never imports from `app.fanout`.
- **`Target` is mutable only via `ingest_snapshot`.** Everything else
  is set at construction. Consumers that need a stable copy should
  snapshot the `worker_set.snapshots` dict themselves.
- **`compute_ui_state` precedence is the spec.** Read top-to-bottom:
  DISABLED → DISABLED_MISCONFIGURED → IDLE → FAILED_OPEN → STARTING →
  RUNNING → DEGRADED (YouTube-with-backup only) → ERRORED.
- **`aggregate_target_health` filters by `expected_worker_roles()`.**
  Snapshots for unexpected roles are silently ignored — Phase 5 must
  tear down workers it no longer expects rather than relying on the
  domain to flag stale state.
- **Domain layer is import-pure.** `tests/domain/test_no_outward_imports.
  py` AST-walks every `app/domain/*.py` and rejects any import from
  `app.repositories`, `app.db`, `app.crypto`, `app.auth`, `app.api`,
  `app.fanout`, `asyncio`, or `subprocess`. Phase 5 cannot accidentally
  reverse the dependency direction.
- **Per-type catalogue is exhaustive.** `TARGET_TYPE_SPECS` covers every
  enum value of `TargetType`; a drift test fails loudly on new values.
- **CircuitBreaker defaults pinned by test.** `max_failures=30`,
  `window=5min`, `healthy_reset_after=60s` — the ADR-0003 values.

---

## Phase 5 — what landed (2026-05-16)

The fan-out engine. ffmpeg child processes per Worker, `-progress`
parsing, log redaction (security boundary), exponential backoff +
breaker, two-TaskGroup architecture (with the Supervisor TG nested
inside the Phase 6 lifespan TG), bounded event bus with drop-oldest
semantics, full SIGTERM choreography on shutdown, and the VK
per-session credential wipe at end of run.

### Design memo first (Rule №3)

A Backend Architect persona answered 12 design questions before any
code:

- **Q1 (Worker protocol):** anyio `MemoryObjectReceiveStream` for
  events, single subscriber (the supervisor); Worker maintains its own
  state and projects to `WorkerSnapshot` via `snapshot()`.
- **Q2 (subprocess):** `asyncio.create_subprocess_exec` over stdlib
  (resolved ADR-0001 open question); `ProcessSpawner` Protocol so
  tests substitute `FakeProcessSpawner` without monkey-patching;
  POSIX-only, no Windows shim.
- **Q3 (RedactionSink):** per-Worker line-buffered byte sanitizer
  AND the process-global `CredentialRegistry` from `app.logging_setup`
  — belt-and-suspenders, both required, covering different attack
  surfaces (raw stderr vs structured logging).
- **Q4 (progress parser):** sync pull-style `feed(bytes) → Iterator[ProgressFrame]`
  with hard 4 KiB line cap; stall detection lives on the Worker, not
  the parser.
- **Q5 (breaker):** per-Worker, owned by the Worker; `compute_backoff`
  is a pure stateless function; the Worker invokes both internally
  and routes the `BreakerVerdict` through Phase 4's `WorkerState`
  transition.
- **Q6 (event bus):** custom `EventBus` over `deque(maxlen=10000)`
  with monotonic drop counter, single drainer; not `asyncio.Queue`
  (no native drop-oldest).
- **Q7 (TaskGroups):** Supervisor.run() owns its own TG; FastAPI
  lifespan (Phase 6) owns the API TG with the supervisor as a child;
  cleanup choreography pinned step-by-step.
- **Q8 (supervisor surface):** event-driven (REST → method) not
  poll-driven; VK per-session credential auto-deleted at end of run;
  audit row on supervisor start.
- **Q9 (logs):** ring buffer (200 lines per Worker) + structlog in
  Phase 5; disk writes deferred to Phase 9 (ADR-0008 amended).
- **Q10 (heartbeat):** event-driven + 1-Hz floor; stale threshold 10 s.
- **Q11 (file map):** added `process.py` (spawn abstraction) and
  `ffmpeg_urls.py` (per-platform URL composition) to CODE_PLAN's list.
- **Q12 (tests):** 12 test files, including AST-walk import-boundary
  test, hypothesis property tests on redaction + parser + backoff +
  bus, FakeProcess + FakeProcessSpawner for subprocess-mocked tests.

### Modules created

```
app/fanout/
├── __init__.py              # public re-exports
├── worker.py                # Worker Protocol + WorkerSpec + WorkerEvent + 5 payload dataclasses
├── process.py               # SpawnedProcess + ProcessSpawner Protocols + AsyncioProcessSpawner
│                            #   (POSIX: start_new_session=True, close_fds=True, stdin=DEVNULL)
├── redaction.py             # RedactionSink — line-buffered byte sanitizer
│                            #   - literal-then-regex two-pass
│                            #   - bridge prefix on force-split (post-review fix H3)
├── progress_parser.py       # ProgressParser — pull-style; eager TypeError, lazy ProgressParseError
├── backoff.py               # compute_backoff(attempt) — pure stateless
├── ffmpeg_args.py           # FFMPEG_BASE_ARGS + build_argv() — locked args
├── ffmpeg_urls.py           # compose_out_url() — per-platform composition
│                            #   - rejects non-rtmp(s):// URLs (post-review fix M4 — argv injection guard)
├── event_bus.py             # EventBus + BusEvent + payload dataclasses
│                            #   - deque(maxlen=10_000), drop-oldest, monotonic counter
├── ffmpeg_worker.py         # FFmpegWorker (v1 Worker impl)
│                            #   - lifecycle loop with breaker + backoff
│                            #   - 4-task pump TaskGroup per spawn (stdout/stderr/stall/wait)
│                            #   - SIGTERM → grace → SIGKILL on stop()
│                            #   - user_reset() from FAILED_OPEN
└── supervisor.py            # Supervisor — top-level orchestrator
                             #   - WorkerFactory Protocol injection point
                             #   - RunState machine (Phase 4)
                             #   - target reconciliation (event-driven, not poll)
                             #   - per-WorkerSnapshot ingest into Target → bus publish
                             #   - VK per-session wipe at end of run
                             #   - 1-Hz heartbeat for /readyz
                             #   - decrypt-failure audit (post-review fix L3)
                             #   - try/except teardown so wipe ALWAYS runs (post-review fix H2)
                             #   - register-then-wrap so orphan secrets are cleaned (post-review fix H1)
                             #   - strong-ref notify task tracking (post-review fix M-1)
```

Tests at `tests/fanout/`:
**525 tests in ~60 s; ruff + ruff format + mypy --strict all clean.**
101 new tests for Phase 5, including:

- 17 redaction tests (incl. `@settings(max_examples=1000)` property test
  asserting registered key never appears in output, AND a 300-example
  force-split-offset property test added in response to the H3 fix)
- 13 progress parser tests
- 14 ffmpeg URL tests (incl. 3 `TestSchemeGuard` for the post-review M4 fix)
- 10 ffmpeg_worker lifecycle tests (with FakeProcess; covers happy
  path, SIGTERM-then-exit, SIGKILL-after-grace, breaker-opens, user_reset,
  stall, redaction propagation)
- 6 supervisor end-to-end tests (run lifecycle, OBS publish events,
  VK credential wipe, heartbeat, bus event publication)
- AST-walk `test_no_inward_imports.py` mirrors Phase 4's domain test:
  `app/fanout/*` may not import from `app.api`.

### Reviewer findings — code-reviewer + security-reviewer ran in parallel (Rule №4)

Both reviewers ran against the diff. All 12 findings applied:

**High / applied:**

- *RedactionSink force-split could leak across MAX_LINE_LEN boundary.*
  A worker URL straddling the cap had its second half emitted
  unredacted (neither chunk contained the full literal; the regex
  required the full pattern in one chunk). **Fix:** wait for
  `MAX_LINE_LEN + url_len` lookahead before splitting; redact the
  wider window in one shot; re-buffer the redacted overflow back into
  `_buf`. New regression test + 300-example property test on the
  force-split path.
- *Orphaned plaintext key in `CredentialRegistry` if `start_run`
  fails mid-spawn.* Register happened before the worker landed in
  `_workers`, so a spawn-side exception leaked the key indefinitely
  in the process-global registry. **Fix:** wrap the spawn block in
  `try/except BaseException` that calls `_unregister_secret` before
  re-raising.
- *VK per-session credential not wiped if `_tear_down_all_workers`
  raised.* The `try/finally` only ran the state transition in
  `finally`; the wipe + close + audit were inside the try. **Fix:**
  capture teardown exception, run wipe + close + audit + state
  transition each under `contextlib.suppress(Exception)`, then
  re-raise the original teardown error. Comment pins "Wipe and close
  MUST run on every path — VK keys are single-use."

**Medium / applied:**

- *Fire-and-forget `asyncio.create_task(bus.notify())` could be
  garbage-collected mid-flight* (asyncio's task ref is weak).
  **Fix:** `_pending_notifies` set holds strong references; done
  callback prunes after completion. Loop-shutdown `RuntimeError`
  is suppressed (final batch is acceptable to lose).
- *`stop_run` guard missed `RunState.STOPPING`* — a concurrent caller
  would fall through to `_set_run_state(STOPPING, STOP_REQUESTED)`,
  raise the typed exception, skip workers + finally, and stick the
  state machine. **Fix:** add `STOPPING` to the early-return tuple.
- *Dead if/else for breaker recording in `_lifecycle_loop`* — both
  branches called `record_failure` identically. **Fix:** collapse to
  one unconditional call with comment explaining "every iteration
  here counts as a failure."
- *Outbound credentials in `/proc/<pid>/cmdline`* — same exposure
  as ADR-0003 §"Ingest credential threat model" accepts for the
  ingest key, but ADR-0003 reasoned only about the ingest credential.
  **Fix:** ADR-0006 amended with "In-process plaintext exposure"
  section explicitly accepting argv exposure inside the container
  trust boundary, and listing the four defence-in-depth measures
  that do matter (start_new_session, close_fds, RedactionSink,
  CredentialRegistry).
- *Custom-target URL with leading `-` could inject ffmpeg flags.*
  Output URL is the last argv; ffmpeg's getopt would parse a
  `-loglevel debug ...` URL as flags. **Fix:** `compose_out_url`
  rejects URLs not starting with `rtmp(s)://`. 3 new TestSchemeGuard
  tests.

**Low / applied:**

- *Loose `Callable[..., FFmpegWorker]` for `worker_factory`.* **Fix:**
  introduced `WorkerFactory` Protocol with the exact `(*, spec)
  -> FFmpegWorker` signature.
- *EventBus `drain()` docstring overpromised per-WS-client backpressure.*
  **Fix:** explicit "Phase-5 scope: ... Phase 6 concern; not yet
  implemented" comment.
- *`close_fds=True` was implicit (CPython 3.7+ default).* **Fix:**
  passed explicitly with comment so a future `pass_fds=` addition
  can't silently start leaking DB/KEK/socket fds into ffmpeg.
- *Decrypt failures only logged, not audited.* **Fix:**
  `_decrypt_failures` list collected during `_load_runnable_targets`
  (which holds an open session); drained after the session closes via
  `await self._audit("credential_decrypt_failed", ...)`. Each audit
  call wrapped in `contextlib.suppress(Exception)` so a failing
  audit doesn't break startup.

**Defended (not applied, with rationale):**

- Documented `_apply_reconnect_verdict` no-op when state is already
  RECONNECTING is correct (the verdict has already been applied by
  the routing earlier).
- The `assert self._super_tg is not None` guard before adding tasks
  is a valid invariant check, not load-bearing — `wait_ready` ensures
  the TG is set before any caller can reach this point.

### Rule №5 audit (post-shipping)

A separate audit agent verified that every claimed reviewer fix, ADR
amendment, module, and test exists on disk and the test bodies assert
meaningful behaviour. **Verdict: clean.** No drift between the "what
landed" narrative and the actual diff.

### Concrete invariants now in the codebase (worth knowing)

- **No plaintext stream key ever appears in an emitted byte stream.**
  Three layers: `RedactionSink` redacts stderr per-line with
  literal+regex (and the new bridge logic for force-split lookahead);
  `CredentialRegistry` redacts every structlog event globally; the
  property test asserts the contract across 1000 random keys at
  arbitrary chunk boundaries AND another 300 examples that target
  the force-split path specifically.
- **Supervisor never blocks on a slow consumer.** `EventBus.put` is
  sync `O(1)`; `deque(maxlen=10_000)` evicts oldest implicitly; drop
  counter is monotonic process-lifetime; `/readyz` (Phase 6) reads it.
- **VK per-session credentials always wiped at end of run.** The
  `stop_run` cleanup runs wipe + close + audit + state transition
  each under `contextlib.suppress(Exception)`, even if
  `_tear_down_all_workers` raised. ADR-0009's PER_SESSION lifetime
  is the only flag that drives the wipe.
- **No subprocess fds leak into ffmpeg.** `close_fds=True` explicit,
  `start_new_session=True` for signal isolation, `stdin=DEVNULL` so
  ffmpeg can't block on stdin EOF. `/proc/<pid>/cmdline` exposure of
  the output URL accepted in ADR-0006 amendment.
- **Custom-target URLs cannot inject ffmpeg flags.** `compose_out_url`
  rejects schemes other than `rtmp(s)://` so a leading `-` URL can
  never reach argv.
- **Per-Worker breaker, owned by the Worker.** `CircuitBreakerPolicy`
  defaults remain ADR-0003 values (30 failures / 5 min / 60 s healthy
  reset); tests use a tighter policy for speed.
- **Backoff is pure stateless.** `compute_backoff(attempt)` with
  optional `rng` for determinism in tests.
- **Two-TaskGroup architecture pinned.** Supervisor TG inside the
  FastAPI lifespan TG (Phase 6 wires the lifespan); on `stop_event`
  fire, the supervisor TG explicitly cancels heartbeat + drop-alert
  tasks so it can exit; worker pump tasks exit naturally when
  `worker.stop()` closes their event channels.
- **Heartbeat = event-driven + 1-Hz floor.** Both update
  `self._heartbeat_ns`; `heartbeat_age()` returns the elapsed
  timedelta; stale threshold is 10 s (HEARTBEAT_STALE_AFTER).
- **Domain layer still pure** — `tests/fanout/test_no_inward_imports.py`
  AST-walks every `app/fanout/*.py` and rejects imports from
  `app.api`. Phase 6 cannot accidentally invert the dependency.
- **Tests stay fast.** 525 tests in ~60 s; the supervisor end-to-end
  tests are the slowest (~5 s each, dominated by Argon2id unlock per
  module-scoped key_material fixture).

---

## Phase 6 — what landed (2026-05-16)

The HTTP / WebSocket surface that wires everything from Phases 1–5
into a runnable FastAPI app. REST routes for auth, targets, run
control, settings, API tokens, sessions history; WebSocket fan-out
with per-client backpressure; loopback-only nginx-rtmp webhooks;
composite /readyz; locked-mode middleware; and the lifespan-owned
two-TaskGroup architecture (API TG nests Supervisor TG).

### Design memo first (Rule №3)

Before any code, the Backend Architect persona answered 15 design
questions in `docs/architecture/phase-6-design-memo.md`. Highlights:

- **Q1/Q2 (app + lifespan):** single `create_app()` factory; single
  lifespan opens one API TaskGroup whose first child is
  `supervisor.run(bus)`. The supervisor's own TG nests inside.
- **Q3 (WS fan-out + backpressure):** per-client `Queue(maxsize=256)`
  with **disconnect-on-overflow** (NOT drop-oldest — that silently
  desyncs the client). On reconnect: synthetic `state.full` payload
  built from supervisor.run_state + supervisor.targets + bus.dropped_total.
- **Q4 (WS auth + envelope):** cookie-only auth (bearer rejected on
  WS v1); versioned envelope `{"v":1, "event": "...", "data": {...}}`;
  custom close codes 4401 (auth) and 4503 (locked).
- **Q5 (locked-mode guard):** **middleware** (NOT router-level
  Depends) so a future router can't forget to declare the guard.
  Allowlist: `/api/unlock`, `/livez`, `/readyz`, `/healthz`,
  `/internal/rtmp/`. Separate unlock rate-limiter from login limiter
  (3 attempts / 15 min per ADR-0010).
- **Q6 (api_tokens.user_id FK):** schema edited in place (Rule №2);
  `get_current_user_via_bearer` loads owner via `token_dto.user_id`;
  hardcoded `"admin"` gone.
- **Q7 (BG rehash):** `BackgroundTasks` consumes `LoginOutcome.needs_rehash`;
  runs after response is sent; failure-tolerant; idempotent under races.
- **Q8 (reprompt burn DoS):** no active mitigation (documented;
  pop-before-validate is correct primitive).
- **Q9 (nginx-rtmp webhooks):** loopback-only via
  `ipaddress.ip_address(peer).is_loopback`; `hmac.compare_digest`
  against current + previous keys.
- **Q10 (errors):** keep Phase 3 `{"detail": "code"}` shape; new
  `ErrorCode` StrEnum centralises the wire codes.
- **Q11 (run start/stop):** **async 202**; supervisor call is
  fire-and-forget tracked in `app.state.supervisor_tasks`; illegal
  state pre-check returns 409.
- **Q12 (/readyz):** `SELECT 1` + supervisor heartbeat + passphrase
  state + (configurable) nginx TCP probe.
- **Q13 (tests):** `httpx.AsyncClient(ASGITransport)` + `FakeSupervisor`
  for the breadth tests; lifespan-wired e2e for the wiring contract.
- **Q15 (module surface):** added three modules vs CODE_PLAN
  (`schemas.py`, `errors.py`, `middleware.py`) plus `app/api/deps.py`
  for Phase-6 dep helpers and `app/fanout/_testing.py` for `FakeSupervisor`.

### Modules created

```
app/
├── api/
│   ├── __init__.py             # re-exports LockedModeMiddleware, ErrorCode, etc.
│   ├── errors.py               # ErrorCode StrEnum + install_exception_handlers
│   │                           #   - IllegalRunStateTransitionError → 409 illegal_run_state
│   │                           #   - ERROR_CODES tuple pinned by test_errors.PHASE_6_CODES_FROZEN
│   ├── schemas.py              # Pydantic v2 request/response models
│   │                           #   - 25+ frozen models, mirrors domain enums
│   │                           #   - WsEnvelope pins v=1
│   ├── middleware.py           # LockedModeMiddleware (BaseHTTPMiddleware)
│   │                           #   - DEFAULT_ALLOWLIST: 5 paths exactly
│   ├── deps.py                 # SupervisorDep, EventBusDep, SettingsDep
│   ├── auth.py                 # auth_router + unlock_router
│   │                           #   - login (+ BG rehash); logout; logout-all
│   │                           #   - change_password (reprompt + constant-time floor 300ms)
│   │                           #   - issue_reprompt_grant (verify password → grant)
│   │                           #   - unlock (own rate limiter; logs exc_type not str(exc))
│   ├── targets.py              # CRUD + credential set/clear + reset-worker
│   │                           #   - DELETE reprompt-protected (DELETE_TARGET)
│   │                           #   - clear_credential reprompt-protected (CLEAR_CREDENTIAL)
│   │                           #   - PUT credential encrypts with build_credential_aad+encrypt
│   │                           #   - every audit call preceded by session.commit()
│   ├── run.py                  # async 202 start/stop + GET /state
│   │                           #   - is_legal pre-check → 409 BEFORE task spawn
│   │                           #   - dropped_total from EventBusDep (not supervisor._bus)
│   ├── settings_api.py         # GET/PATCH + rotate-ingest-key (reprompt)
│   │                           #   - GET masks ingest key to last4 only
│   ├── api_tokens_api.py       # list / create / revoke
│   │                           #   - create returns plaintext ONCE
│   │                           #   - revoke reprompt-protected
│   ├── sessions_history.py     # GET /api/sessions
│   │                           #   - cursor pushed to SQL (before parameter)
│   ├── internal_rtmp.py        # /publish + /publish_done (loopback-only)
│   │                           #   - ipaddress.ip_address(peer).is_loopback check
│   │                           #   - _keys_match runs BOTH compare_digest unconditionally
│   ├── health.py               # /livez /readyz /healthz
│   │                           #   - SELECT 1 + heartbeat + passphrase + (opt) nginx
│   │                           #   - get_running_loop() (not deprecated get_event_loop)
│   └── ws.py                   # /ws + ws_broadcaster + WsRegistry
│                               #   - cookie verify via repo direct (no SessionAuthService)
│                               #   - accept() BEFORE registry.add (no orphan subscriber)
│                               #   - Queue(maxsize=256), disconnect-on-overflow
├── fanout/
│   └── _testing.py             # FakeSupervisor (public test surface)
└── main.py                     # create_app + lifespan + uvicorn entrypoint
                                #   - KeyMaterial built in create_app (NOT lifespan)
                                #   - LockedModeMiddleware registered in create_app
                                #   - lifespan: AuthState, Supervisor, EventBus, TaskGroup
                                #   - TG children: supervisor.run / ws_broadcaster / pruner
```

Plus amendments:
- `app/db/schema.sql` — `api_tokens.user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE` + `idx_api_tokens_user_id`
- `app/db/models.py` — `ApiTokenORM.user_id` mapped + index
- `app/repositories/api_tokens.py` — `ApiTokenDTO.user_id`; `create(*, user_id, ...)`
- `app/auth/api_tokens.py` — `ApiTokenService.create(*, user_id, label)`
- `app/auth/deps.py` — `AuthState.unlock_rate_limiter`; `get_current_user_via_bearer` loads owner via FK
- `app/auth/reprompts.py` — new `RepromptScope.CLEAR_CREDENTIAL`
- `app/config.py` — `healthz_check_nginx: bool = False`
- `pyproject.toml` — `python-multipart` dep
- `app/repositories/sessions_history.py` — `list_recent(limit, *, before=None)` (cursor at SQL)

Tests at `tests/api/`:
**610 tests in ~80s; ruff + ruff format + mypy --strict all clean.**
85 new tests for Phase 6, including:

- 13 middleware tests (allowlist helpers + lock-state E2E + parametrized
  coverage over every real Phase 6 router path)
- 6 error-code tests (frozen contract pinning)
- 14 auth tests (login, logout, unlock, reprompt, change-password E2E)
- 12 targets tests (CRUD, credential set/clear w/ reprompt, reset-worker)
- 6 run tests (state read, 202 start/stop, double-start 409)
- 3 internal_rtmp tests (key match, wrong-key reject, publish_done)
- 4 health tests (/livez, /readyz happy + stale, /healthz alias)
- 3 WS tests (auth-fail 4401, state.full snapshot, locked 4503)
- 8 settings + api_tokens + sessions tests

### Reviewer findings — code-reviewer + security-reviewer ran in parallel (Rule №4)

Both reviewers ran against the diff. **One critical + four highs + three
mediums + two lows + three code-review highs + three code-review mediums
applied; the BaseHTTPMiddleware-vs-ASGI improvement deferred as
non-blocking robustness.**

**Critical / applied:**

- *`LockedModeMiddleware` never installed.* The original code called
  `app.add_middleware(LockedModeMiddleware, ...)` inside the lifespan,
  but Starlette builds the middleware stack BEFORE the lifespan runs,
  so `add_middleware` raises `RuntimeError`. **Fix:** construct
  `KeyMaterial` in `create_app` (LOCKED state) and register the
  middleware before returning the app. The lifespan unlocks the same
  instance under env mode; the middleware reads `state` per request,
  so `/api/unlock` takes effect immediately.

**High / applied:**

- *WS subscriber registered before `accept()`.* If accept() raised,
  the broadcaster would enqueue events into an orphaned queue until
  the finally block ran. **Fix:** reorder accept→register.
- *`asyncio.get_event_loop()` is deprecated in async contexts.* **Fix:**
  use `asyncio.get_running_loop()` in `_check_nginx_rtmp`.
- *Sessions-history pagination cursor was Python-side, not SQL-side*,
  giving a false "last page" signal whenever an in-window row failed
  the filter. **Fix:** push `before` into the repo's SQL `WHERE`.
- *Loopback check used string set `{"127.0.0.1", "::1", "localhost"}`*
  — `"localhost"` is dead (ASGI scope has numeric IP) and `::ffff:127.0.0.1`
  (IPv4-mapped IPv6) was rejected. **Fix:** parse via
  `ipaddress.ip_address(peer).is_loopback`.
- *WS handler constructed `SessionAuthService` just to verify cookie*,
  leaking the login rate-limiter handle onto the unauthenticated WS
  path. **Fix:** call `compute_session_token_hash` +
  `HttpSessionsRepository.get_by_token_hash` directly.
- *`clear_credential` was not reprompt-protected*, letting a stolen-
  cookie attacker silently drop streams. **Fix:** new
  `RepromptScope.CLEAR_CREDENTIAL`; handler consumes the grant before
  the delete.
- *`change_password` had a timing channel*: failure path returned
  ~100 ms after verify; success path took ~200 ms (verify + hash +
  revoke). **Fix:** `CHANGE_PASSWORD_TIMING_FLOOR_SECONDS=0.30` floor
  via `asyncio.sleep` in `finally`.

**Medium / applied:**

- *Middleware allowlist was `/internal/`* (broader than the documented
  `/internal/rtmp/*`). **Fix:** tighten to `/internal/rtmp/`.
- *`_dropped_total_from(supervisor)` reached into `supervisor._bus`*
  (private attribute). **Fix:** wire `EventBusDep` and read
  `bus.dropped_total` directly.
- *No parametrized middleware test covered the real Phase 6 router
  surface.* **Fix:** new tests at
  `tests/api/test_middleware.py::test_real_router_blocked_in_locked_mode`
  and `::test_real_health_routes_reachable_in_locked_mode` iterate
  over `NON_ALLOWLISTED_PROBES` and `ALLOWLISTED_PROBES` and assert
  the middleware decision for each.

**Low / applied:**

- *Unlock failure logged `exc=str(exc)`* — Argon2 / HKDF exception
  strings may include parameter values that leak into operator-shared
  disk logs. **Fix:** log `exc_type=type(exc).__name__`.
- *`_keys_match` short-circuited on current-key match*, leaking timing
  about which key matched. **Fix:** always run both `compare_digest`
  calls and OR the booleans.

**Deferred (non-blocking robustness):**

- *`BaseHTTPMiddleware` should be replaced by pure ASGI middleware*
  for the locked-mode guard (Starlette best-practice for new code).
  Defended for now: the middleware is short and correctly intercepts
  every request. Convert when adding the next middleware (e.g., a
  Phase 7 SPA static-file handler).

### Rule №5 audit (post-shipping)

A separate audit agent verified every claimed reviewer fix, schema
edit, module, and test exists on disk with the expected behaviour.
**Verdict: clean.** Zero drift between the "what landed" narrative
and the actual diff. Pipeline confirmed: ruff + ruff format + mypy
--strict all clean; pytest 610 passed in ~80s.

### Concrete invariants now in the codebase (worth knowing)

- **Locked-mode middleware is unconditional.** Registered in
  `create_app` so a future router automatically inherits the guard.
  `DEFAULT_ALLOWLIST` is pinned by `test_middleware::test_default_allowlist_pins`
  — expanding it requires a code change the security review can
  catch at PR time. Parametrized tests assert every real Phase 6
  router gets `service_locked` in locked mode.
- **All four Phase 3 deferred gaps are closed:**
  (1) router-level locked-mode guard — see middleware above;
  (2) `api_tokens.user_id` FK + hardcoded "admin" gone;
  (3) BackgroundTasks rehash wired in `login()`;
  (4) reprompt burn DoS documented in design memo §Q8.
- **No `SessionAuthService` on the WS path.** Cookie verification
  uses `compute_session_token_hash` + `HttpSessionsRepository` directly,
  so unauthenticated WS connect attempts cannot DoS the login limiter.
- **Loopback check parses, doesn't string-match.** `127.0.0.0/8`,
  `::1`, and `::ffff:127.0.0.1` (dual-stack form) all accepted.
  `"localhost"` is intentionally NOT accepted (peer is always numeric).
- **Reprompt-protected destructive actions:** DELETE target,
  clear credential, change password, rotate ingest key, revoke API
  token. Reset-worker is NOT reprompt-protected (recovery action,
  not destructive). Set credential is NOT reprompt-protected
  (user-driven knob, no surprise destruction).
- **Run start/stop is async 202.** REST returns immediately; client
  watches WS `run.state.changed` for the transition.
- **Per-WS-client `Queue(maxsize=256)` with disconnect-on-overflow.**
  NOT drop-oldest — that would silently desync the client. On overflow
  the WS closes with code 1011; the client reconnects and receives a
  fresh `state.full` snapshot.
- **Cursor pagination pushed to SQL.** `list_recent(limit, *,
  before=None)` applies the `before` filter in `WHERE`, so the
  `next_before` cursor is correct by construction even when many
  in-window rows fail the filter.
- **`change_password` constant-time floor 300 ms.** Wrong-password and
  correct-password paths take the same wall-clock time, even though
  the success path runs verify + hash + revoke_all + commit.
- **Ingest key timing-safe compare against both current and previous.**
  `_keys_match` runs both `hmac.compare_digest` calls unconditionally
  so the wall-clock leaks nothing about which key matched.
- **The middleware stack is built ONCE in `create_app`.** Tests build
  the app identically to production via `_make_test_app`; the
  middleware-registration site is the same code path.
- **Pipeline stays fast.** 610 tests in ~80s. Slowest tests are the
  WS authenticated path (one cookie-login round-trip per test, ~600 ms)
  and the change-password e2e (one Argon2 verify + hash, ~250 ms).

---

## Phase 7 — what landed (2026-05-16)

The runnable SPA shell. Theme system, router, auth wiring, login + unlock
screens, dashboard placeholder. Phases 8 (Dashboard) and 9 (Settings)
inherit the patterns locked here.

### Design memo locked first (Rule №3)
- Three agency-agent passes ran in parallel: **UX Architect** (IA, auth
  flow, theme bootstrapping, focus management), **UI Designer** (Tailwind v4
  `@theme` block, component classing, ThemeToggle visuals), **Software
  Architect** (api client shape, TanStack defaults, session pattern, WS
  reconnect machine, bundle budget).
- Synthesis at `docs/architecture/phase-7-design-memo.md` — 16 locked
  decisions across §§ A (backend corrections), B (routing), C (theme),
  D (CSS architecture), E (api client + state), F (WS plumbing),
  G (Login UX), H (Locked/Unlock), I (messages discipline), J (components),
  K (a11y wiring), L (folder layout), M (bundle guard), N (open
  follow-ups), O (critical "must not get wrong"), P (acceptance criteria).

### Backend delta (1 endpoint, 4 tests)
- **`GET /api/auth/me`** added at `app/api/auth.py`. Returns
  `UserSummary` (200) when the cookie resolves to a live session; 401
  otherwise. Load-bearing for `<RequireAuth>` — without it, the SPA on
  hard-reload has no clean way to learn its own auth state.
- `tests/api/test_auth.py` + 3 tests covering /me; `tests/api/test_middleware.py`
  NON_ALLOWLISTED_PROBES + /api/auth/me — locked-mode coverage. 610 → **614** tests green.

### Frontend modules shipped (33 files under `web/src/`)
```
web/src/
├── main.tsx                       # QueryClient + RouterProvider; imports tokens.css first
├── App.tsx                        # <GlobalErrorHandler> — bus subscriber (401 → /login, 503 → /unlock)
├── router.tsx                     # routes[] (RouteObject[]); lazy Settings
├── messages.ts                    # typed t() helper + as-const messages tree
├── vite-env.d.ts                  # ImportMetaEnv shim (VITE_BUILD_SHA)
├── theme/
│   ├── tokens.css                 # @theme before @import "tailwindcss"; dark + system + focus-ring layer
│   ├── ThemeManager.ts            # localStorage + matchMedia + storage event listeners
│   └── ThemeToggle.tsx            # 3-segment radio group; Phosphor Sun/Moon/Monitor; honors useReducedMotion
├── lib/
│   ├── cn.ts                      # clsx wrapper
│   ├── api.ts                     # apiFetch<T>(schema?) + ApiError + silenceGlobalErrors opt-out
│   ├── errors.ts                  # ErrorCode union (21 codes mirroring app/api/errors.py)
│   ├── eventBus.ts                # createEmitter<T>()
│   ├── queryClient.ts             # QueryClient defaults + meta.silenceGlobalErrors guard on caches
│   ├── safeNext.ts                # open-redirect-safe ?next / state.from validator
│   ├── useReducedMotion.ts        # matchMedia hook (used by ThemeToggle; Phase 8 inherits)
│   ├── ws.ts                      # StatusStream singleton — backoff, 4401/4503/1011 close codes, envelope v:1
│   └── schemas/{_shared,auth,health}.ts  # Zod schemas; per-domain layout
├── components/
│   ├── Button.tsx                 # variants primary/danger/secondary/ghost/link × sm/md/lg/xl
│   ├── Banner.tsx                 # info/warn/error variants
│   ├── Skeleton.tsx               # Skeleton + .Tile + .Row + .Pill
│   ├── EmptyState.tsx             # centered icon + heading + body + action
│   ├── AppShell.tsx               # header (wordmark + RunStateBadge stub + ThemeToggle + AccountMenu) + main + footer
│   ├── AuthLayout.tsx             # no-header centered card backdrop (Login + Unlock)
│   └── RequireAuth.tsx            # useQuery(["auth","session"]) → /login or /unlock or render children
└── pages/
    ├── Login.tsx                  # hidden username for password managers; Caps Lock hint; permissive zod; ?next safe
    ├── Unlock.tsx                 # passphrase paste form; Retry-After-driven countdown banner
    ├── DashboardPlaceholder.tsx
    ├── SettingsPlaceholder.tsx    # lazy-loaded; "Coming in Phase 9"
    └── NotFound.tsx
web/scripts/
└── check-bundle-size.mjs          # postbuild guard — fails if total gzipped > 250 KB
```

### Tooling deltas
- **ESLint v9 flat config** (`web/eslint.config.js`). `.eslintrc.cjs`
  removed (Rule №2 — no migration baggage; greenfield to the modern
  format directly).
- **`react/jsx-no-literals`** rule enforced — every user-facing string
  goes through `messages.ts::t()`. `ignoreProps: true` + a tiny glyph
  allowlist absorbs the inevitable false positives.
- **Bundle budget enforced** by `npm run build` → `node scripts/check-bundle-size.mjs`.
  Current: **139 KB gzipped** (react-vendor 34, main 96, css 5, Settings chunk 3) vs **250 KB budget**.

### Reviewer findings — code-reviewer (1 high + 3 mediums + 3 lows + nits)

**High / applied:**
- *H-1: `role="menu"` ARIA contract violation* — AccountMenu's dropdown
  `<div>` had `role="menu"` but the bare `<button>` children weren't
  `role="menuitem"`. **Fix:** dropped the `role="menu"` and let the
  native `<details>/<summary>` disclosure semantics carry the
  affordance. No keyboard menu pattern needed for Phase 7's three-item
  account menu.

**Medium / applied:**
- *M-1: `MutationCache.onError` re-broadcasts errors regardless of
  `silenceGlobalErrors`* — Login/Unlock would briefly show "unexpected
  error" before the global handler navigated. **Fix:** added
  `meta.silenceGlobalErrors` opt-out and guarded both `QueryCache` and
  `MutationCache` `onError` to skip rebroadcast for opted-out
  queries/mutations. Login, Unlock, Logout, and the RequireAuth session
  probe all set the meta.
- *M-2: `useReducedMotion` documented Phase 7 consumer was nonexistent* —
  ThemeToggle was relying on the global `@media (prefers-reduced-motion)`
  CSS rule. **Fix:** wired `useReducedMotion()` in ThemeToggle so the JS
  hook is the source of truth for the segment transition class.
- *M-3: `--color-bg-overlay` token missing from `tokens.css`* — defined
  in design-system §1 but absent. No Phase 7 consumer, but Phase 8
  dialogs/slide-outs reach for it. **Fix:** added the token to light +
  dark + system blocks with the spec values.

**Low / applied:**
- *L-3: `--ext ts,tsx` is a no-op in ESLint v9 flat config* — **Fix:**
  removed from the `lint` script. Flat config's `files` glob handles it.
- *L-1 + L-2:* documentation gaps, no functional change required.

**Deferred (non-blocking):**
- *N-1: mixed absolute/relative path conventions in `apiFetch` calls.*
  Documenting the contract in `api.ts`'s JSDoc as a follow-up is enough;
  not chasing per-call rewrites for a stylistic split that won't bite.
- *N-3: `themeManager.init()` storage-listener lacks a `typeof window`
  guard* (the matchMedia listener has one). Pure-SPA, no SSR; defer
  until/if a Node test environment imports the module directly.

### Rule №5 audit (post-shipping)
A separate audit agent verified every claimed reviewer fix, schema edit,
module, and test exists on disk with the expected behaviour.
**Verdict: clean.** Zero drift between the "what landed" narrative and
the actual diff. Pipeline confirmed: backend 614 pytest green; frontend
typecheck + lint + build clean at 139 KB gzipped.

### Concrete invariants now in the codebase (worth knowing)

- **`@theme` always precedes `@import "tailwindcss"`.** Reverse order
  and v4 stops generating utilities silently — the #1 footgun.
  `tokens.css` lines 19 vs 113 hold the contract.
- **Hidden username `<input>` in Login.** `autoComplete="username"
  value="admin" readOnly hidden` so password managers correctly
  associate the saved credential. Removing this silently breaks
  credential saving across browsers.
- **No `document.cookie` reads anywhere in `web/src/`.** Auth state
  derives from API responses (200/401) only — the HttpOnly cookie is
  invisible to JS by design.
- **Single `aria-live="polite"` region in the run-state badge** (even
  while stubbed `OFFLINE`). Page-title announcer is a SEPARATE live
  region — different concerns, design-system §9 permits.
- **`safeNext()` validates `?next=` / `state.from` to same-origin path
  only.** Allowlist regex, not negative-lookbehind. Habit matters.
- **Locked-mode is a route, not a render mode.** `/unlock` is reached
  via the global bus's 503 → `navigate("/unlock")` redirect; deep-link
  intent is preserved in `state.from`.
- **`meta.silenceGlobalErrors` is the cache opt-out** for inline-handled
  mutations/queries. Set it AND pass `silenceGlobalErrors: true` to
  `apiFetch` — both layers must opt out for the suppression to hold.
- **TanStack defaults are conservative for an appliance UI:**
  `refetchOnWindowFocus: false`, `staleTime: 30s`, never retry on 401/
  403/422/429, single retry on 5xx, mutations never retry.
- **Bundle stays small.** 139 KB gzipped total; lazy Settings chunk is
  3 KB; react-vendor is 34 KB. Headroom: 111 KB before the budget bites.

### Open follow-ups (logged, not blocking)

- **Self-hosted Inter Variable + JetBrains Mono Variable.** Phase 7
  ships with the platform-font stack only (`system-ui`, `ui-monospace`).
  Land the woff2 files + `@font-face` declarations before Phase 9
  surfaces credential-display widgets where the monospace is most
  visible.
- **`BaseHTTPMiddleware` → pure ASGI middleware** (still deferred from
  Phase 6). Convert when adding the next middleware (e.g., Phase 11
  Cache-Control on the SPA index.html).
- **`needs_password_change` flow** intentionally NOT built — backend has
  no field. If product wants it, file an ADR and add
  `users.password_must_rotate` + `UserSummary.password_must_rotate`
  together.
- **WebSocket bearer-token auth** (Phase 6 design memo §Q4): cookie-only
  on WS for v1; revisit if a scripted/headless consumer wants WS.

---

## Phase 8 — what landed (2026-05-16)

The runnable Dashboard. Replaces the Phase-7 `DashboardPlaceholder`
with the real `Dashboard` page: hero START/STOP card, per-target tile
grid, slide-out details panel, WS-driven live status, VK paste
mid-flow, RecentEventsMenu, ReconnectingBanner, first-run hint.

### Design memo locked first (Rule №3)
- Three agency-agent passes ran in parallel: **UX Architect**
  (15 questions covering hero state matrix, VK flow, slide-out focus,
  first-run hint persistence, RecentEventsMenu state owner, Toast vs
  Events double-announce), **UI Designer** (15 questions covering
  RunStateBadge visuals, HeroCard spacing, START/STOP morph
  choreography, TargetTile visuals per state, Sparkline DPR + theme
  handling, LogViewer auto-scroll detection, reduced-motion checklist
  per Phase 8 element), **Software Architect** (15 questions covering
  TanStack-as-single-source-of-truth, hook surface, mutations,
  WS-to-cache patching, Zod validation, Vitest scope, lifecycle of
  `<StatusStreamHost>`, ErrorBoundary scope, bundle-budget projection).
- Synthesis at `docs/architecture/phase-8-design-memo.md` — 25 locked
  sections (A backend prereq, B data flow, C query keys, D hook
  surface, E Zod schemas, F WS→cache patching, G mutations, H badge,
  I HeroCard, J VK paste, K confirm-on-stop, L tile + slide-out,
  M Vitest, N reusable components, O RecentEvents store, P toast/events
  policy, Q first-run hint, R `<StatusStreamHost>` lifecycle, S no
  polling fallback, T folder layout, U bundle projection, V
  reduced-motion checklist, W "must not get wrong" list, X acceptance,
  Y doc-pass follow-ups).

### Backend delta (1 field + 1 supervisor method + 4 tests)
- **`RunStateView.run_state_changed_at: AwareDatetime | None`** added
  at `app/api/schemas.py`. Load-bearing for the hero LIVE timer —
  without it a hard reload mid-LIVE would tick from zero.
- **`Supervisor._run_state_changed_at`** + public `run_state_changed_at()`
  getter; bump happens in `_publish_run_state` (the single choke point
  through which all transitions funnel, both `_set_run_state` and the
  OBS publish-notification fast paths).
- `FakeSupervisor` mirrors the field + getter; `_transition_to` bumps.
- `GET /api/run/state` and WS `state.full` payloads populate the new
  field.
- Tests: `tests/api/test_run.py` +2 cases; `tests/api/test_ws.py` +1
  assertion; `tests/fanout/test_supervisor.py::TestRunStateChangedAt`
  (1 class with 1 test that drives start→armed→stop and asserts the
  field is None at boot, non-None after first transition, monotonically
  bumped). 614 → **616** tests green.

### Frontend modules shipped (19 new + 3 modified + tokens.css/messages.ts updates)

```
web/src/
├── pages/
│   └── Dashboard.tsx                  # composes hero + tile grid + slide-out + banner
├── hooks/
│   ├── useRunState.ts                 # TanStack query on ["run","state"]; derives runStartedAt
│   ├── useTargets.ts                  # joins ["targets"] with ["run","state"].targets snapshots
│   └── useWsConnectionStatus.ts       # useSyncExternalStore on statusStream.onStatus
├── lib/
│   ├── wsReducer.ts                   # pure applyWsEventToCache(prev, event)
│   ├── wsReducer.test.ts              # Vitest 9 cases (4 events × 2 prev-states + race)
│   └── schemas/
│       ├── run.ts                     # RunState, TargetUiState, WorkerState, WorkerRole, etc.
│       ├── targets.ts                 # TargetType, Target, CredentialSummary
│       ├── settings.ts                # SettingsView
│       └── wsEnvelopes.ts             # discriminated union WsKnownEvent
└── components/
    ├── RunStateBadge.tsx              # replaces Phase 7 stub IN AppShell slot
    ├── HeroCard.tsx                   # START/STOP per RunState + morph + VK gate + first-run hint
    ├── TargetTile.tsx                 # one-button tile w/ Phosphor status icons + MetricGrid
    ├── TargetDetails.tsx              # Radix Dialog modal=false slide-out w/ focus return
    ├── Sparkline.tsx                  # canvas, DPR-scaled, theme-aware via MutationObserver
    ├── LogViewer.tsx                  # mono + auto-scroll + Pause-tail
    ├── MetricGrid.tsx                 # 2- or 4-col tabular grid
    ├── InlinePromptCard.tsx           # VK paste mid-flow; passive validation; lg buttons
    ├── RecentEventsMenu.tsx           # bell + unseen dot + Escape close
    ├── ReconnectingBanner.tsx         # info Banner; 600ms grace before render
    ├── ErrorBoundary.tsx              # in-house 35-line class component
    ├── StatusStreamHost.tsx           # owns WS lifecycle + cache patching + RecentEvents push
    └── RecentEventsProvider.tsx       # React Context + useReducer; capped at 50

# Modified (Phase 7 → Phase 8 rewires)
web/src/router.tsx                     # added RecentEventsProvider + StatusStreamHost siblings
web/src/components/AppShell.tsx        # real RunStateBadge + RecentEventsMenu in header
web/src/messages.ts                    # +runState, hero, dashboard, tile, targetDetails, logViewer, recentEvents sections
web/src/theme/tokens.css               # badge-live-pulse keyframe + extended focus-ring + spinner reduced-motion exception
web/vite.config.ts                     # Vitest test block (environment: "node")
web/package.json                       # +vitest devDep + "test" script

# Deleted (Rule №2 — no migration baggage)
web/src/pages/DashboardPlaceholder.tsx
```

### Tooling deltas
- **Vitest** added as a dev dep (Phase 7 had no frontend tests). Scope
  is deliberately narrow: pure-function tests for `applyWsEventToCache`
  only (`environment: "node"`, no jsdom, no `@testing-library/react`).
  Component tests deferred to a future Phase 8b. The pure-function
  approach surfaced the highest-risk code without dragging in DOM
  testing infrastructure.
- **`npm run test` floor:** typecheck && lint && build && test must all
  pass.
- **Bundle**: 139 → **168 KB gzipped** (+29 KB for Phase 8 surface).
  Predicted ~170 KB; actual 168 KB. ~30% headroom under the
  **256 KB** budget. No lazy splitting required for Phase 8.

### Reviewer findings — code-reviewer (2 highs + 3 mediums + 1 low)

**High / applied:**
- *H-1: `aria-live` on a `<button>` is invalid ARIA* — ARIA 1.2 §6.2.14
  prohibits live-region attributes on interactive roles; screen readers
  ignore the announcement when focus is on the element. RunStateBadge
  had collapsed Phase 7's `<div role="status" aria-live="polite">`
  wrapper into the button itself. **Fix:** restored the wrapping
  `<div>` so the live region is a non-interactive ancestor — matches
  the Phase 7 stub contract verbatim.
- *H-2: `useTargets` `getQueryData` lacks reactivity* — reading
  `["run","state"]` via `queryClient.getQueryData` inside a `useMemo`
  doesn't re-render on WS patches (queryClient identity is stable).
  **Fix:** replaced with a sibling `useQuery` against the same key
  (TanStack dedupes the queryFn against `useRunState`'s call;
  `setQueryData` notifies both subscribers). Removed the dead-code
  `const _ = useRunState(); void _;` dance at the same time (L-1).

**Medium / applied:**
- *M-1: VK paste `autocomplete="off"`* — ignored by Chrome / Firefox on
  password fields. **Fix:** `autoComplete="new-password"` (the
  documented suppression value).
- *M-3: HeroCard live-ambient ring double-wraps `hsl()`* — Phase 7
  source-form deviation ships full `hsl(...)` values in
  `--color-*-faint` tokens; `hsl(var(--color-live-faint))` produces
  `hsl(hsl(...))` — invalid CSS, ring silently never rendered. **Fix:**
  `var(--color-live-faint)` directly.
- *M-4: Slide-out focus-return broken* — Radix `modal={false}` has no
  `Dialog.Trigger`, so the default `onCloseAutoFocus` drops focus to
  `document.body`. **Fix:** threaded a `triggerRef: RefObject<HTMLButtonElement | null>`
  through `TargetDetails`; `TargetTile.onOpen` signature changed to
  `(button: HTMLButtonElement) => void` so the click handler passes
  `e.currentTarget`; Dashboard's `useRef` is populated on each tile open
  and consumed by an explicit `onCloseAutoFocus={(e) => { e.preventDefault(); triggerRef.current?.focus(); }}` override.

**Low / applied:**
- *L-2: RecentEventsMenu missing Escape handler + `aria-haspopup`* —
  ARIA APG `Disclosure` pattern requires Escape close. **Fix:** added a
  global `keydown` listener inside the open-effect; added
  `aria-haspopup="menu"` to the trigger button.

### Rule №5 audit (post-shipping)
A separate audit agent independently verified every claimed reviewer
fix, backend schema edit, module, and pipeline number on disk.
**Verdict: clean.** All six fixes verified at cited line numbers; all
spot-checked memo invariants (§A backend delta, §W#1-3 + §W#11 + §N + §M + §T)
hold; pipeline numbers match exactly (616 backend pytest, 9/9 vitest,
168,029 B gzipped, typecheck + lint silent).

### Concrete invariants now in the codebase (worth knowing)

- **`useStatusStream` (inside `<StatusStreamHost>`) is the SOLE writer
  to `["run","state"]`** outside its own `queryFn`. Mutations never
  `setQueryData` for run-state — they let the server's WS event
  refresh the cache. Verified: 5 `setQueryData` call sites in
  `web/src/`, exactly 1 targets the run-state key.
- **Every WS envelope `data` is Zod-validated** via
  `WsKnownEvent.safeParse({event, data})` before reaching the cache.
  Unknown events drop silently (forward-compat).
- **Single `aria-live` region for run-state** — RunStateBadge's
  wrapping `<div role="status" aria-live="polite">` is the only one
  (preserved from Phase 7 stub contract). Page-title announcer in
  AppShell is a SEPARATE concern; Banner's parameterised `aria-live`
  is reusable infrastructure, not a duplicate.
- **`runStartedAt` survives reconnect** — derived from
  `RunStateView.run_state_changed_at` (the §A backend delta), present
  on both `GET /api/run/state` and WS `state.full`.
- **VK paste flow is PUT-credential-then-POST-start** (not "keys in
  the start payload" as the old ux-flows.md said). Skip-this-time POSTs
  start directly; the supervisor's `_credentials_satisfied` gate routes
  credential-less VK targets to `DISABLED_MISCONFIGURED` for the
  session, no special server path needed.
- **Slide-out URL state via `?target=<id>`** + activator-ref pattern.
  Unknown id silently closes (does NOT throw). Browser back closes the
  slide-out. Focus returns to the tile button via explicit
  `onCloseAutoFocus` override.
- **First-run hint dismissal** is `localStorage["restream-plus.firstRunHintDismissed"]`
  (read on mount, set on first transition to `live`). Backend has no
  flip-the-flag handler yet — Phase 9 backlog item.
- **Hero `xl` button reserved for START/STOP** (F# UI-C.start-width).
  InlinePromptCard's buttons are `lg`. Verified: only `HeroCard`'s
  `HeroButton` uses `size="xl"`.
- **Phosphor icons everywhere for tile/badge status** — design-system
  §6.3 unicode notation (●/◐/✕/⚠) is mockup-only; F# UI-E.theme-svg
  extends here for the same cross-OS rendering reason that drove
  Phase 7's theme-toggle.
- **TanStack defaults inherited:** new queries override only
  `staleTime` (Infinity for `["run","state"]`, 30s default for
  `["targets"]`). Mutation cache + query cache `meta.silenceGlobalErrors`
  opt-out (Phase 7 §E pattern) still in force — Phase 8's start/stop
  mutations do NOT opt out; 401 → global handler → /login is desired.
- **WS-cache reducer is a pure function** in `lib/wsReducer.ts`. No
  console, no Date.now, no globals. 9 Vitest cases cover the 4 event
  types × seeded/empty prev + the race-tolerance append path.
- **Bundle: 168 KB gzipped** of 256 KB budget. Plenty of headroom for
  Phase 9 Settings (lazy chunk grows; main chunk small additions).
- **No lazy splitting in Phase 8** — `TargetDetails` body could be the
  first candidate if the budget tightens later.

### Open follow-ups (logged, not blocking)

- **Backend Phase 9 backlog:** `POST /api/settings/mark-first-run-complete`
  or `SettingsUpdateRequest.first_run_complete`; until shipped, the
  localStorage flag is the sole dismissal source.
- **Backend Phase 9 backlog:** ingest-reveal endpoint (`POST /api/settings/reveal-ingest-key`,
  reprompt-protected) so the hero `offline` body can show the
  plaintext stream key once. Phase 8 shows masked + a "Reveal in
  Settings" link.
- **Backend Phase 9+ backlog:** surface ingest-side metrics
  (host/port + ingest-worker bitrate/fps/drops). Phase 8 falls back to
  aggregate target metrics in the hero LIVE body.
- **Doc-pass:** design-system §1 (raw HSL components vs full hsl()),
  §6.3 (unicode tile status), §6.9 (token-wrapping note), §2
  (`--text-display` description), and ux-flows.md §2 VK step 5 ("keys
  in start payload") — all flagged in phase-8-design-memo §Y.
- **Phase 8b (deferred):** add `@testing-library/react` + component
  tests for HeroCard / TargetDetails / Dashboard interactions once
  Phase 9 stabilises the chrome.

---

## File map

```
d:\Projects\Programming\Restream_Plus\
├── README.md                       # public-facing project description
├── LICENSE                         # Apache-2.0
├── .gitignore, .gitattributes      # gitattributes pins LF for shell+s6+Dockerfile
├── .dockerignore                   # default-deny allowlist
├── pyproject.toml                  # backend deps + tooling (ruff, mypy, pytest)
├── .git/                           # repo initialized 2026-05-17; tracks
│                                   #   origin/main at github.com/pelmentor/Restream_Plus
├── .venv/                          # local Python 3.12 venv (NOT in version control)
├── app/                            # PHASES 1 + 2 + 3 + 4 LANDED — see "what landed" sections
│   ├── __init__.py
│   ├── version.py
│   ├── config.py
│   ├── logging_setup.py
│   ├── crypto/{__init__,kdf,aead,passwords}.py
│   ├── db/                         # PHASE 2
│   │   ├── __init__.py
│   │   ├── schema.sql              # canonical schema (source of truth)
│   │   ├── types.py                # UTCEpochSeconds TypeDecorator
│   │   ├── models.py               # ORM mappings
│   │   ├── engine.py               # async engine + PRAGMAS
│   │   ├── session.py              # async_sessionmaker
│   │   └── schema_init.py          # bring-up + crash recovery
│   ├── repositories/               # PHASE 2 — 8 typed Pydantic-DTO repos
│   │   ├── __init__.py
│   │   ├── users.py
│   │   ├── sessions.py
│   │   ├── api_tokens.py
│   │   ├── targets.py
│   │   ├── credentials.py
│   │   ├── settings_repo.py
│   │   ├── audit_log.py
│   │   ├── sessions_history.py
│   │   └── _converters.py          # PHASE 4 — DTO → domain adapter (only module
│   │                               #   that imports from both repositories and domain)
│   ├── auth/                       # PHASE 3 — auth surface
│   │   ├── __init__.py
│   │   ├── key_material.py         # locked/unlocking/ready state machine
│   │   ├── sessions.py             # cookie login + verify + revoke + rate-limit
│   │   ├── api_tokens.py           # bearer issue/verify/revoke
│   │   ├── rate_limit.py           # paired sliding window + client-IP extractor
│   │   ├── reprompts.py            # 60s scope-bound grants
│   │   ├── usernames.py            # NFKC + casefold normalization
│   │   └── deps.py                 # FastAPI Depends + AuthState
│   ├── domain/                     # PHASE 4 — pure-Python state machines + value objects
│   │   ├── __init__.py
│   │   ├── errors.py               # IllegalStateTransitionError + 2 subclasses
│   │   ├── credential_policy.py    # CredentialLifetime + lifetime_for()
│   │   ├── target_types.py         # TargetType + TargetTypeSpec catalogue
│   │   ├── circuit_breaker.py      # BreakerVerdict + CircuitBreaker (sliding window)
│   │   ├── run_state.py            # RunState + RunAction + transition()
│   │   ├── worker_state.py         # WorkerState + WorkerAction + WorkerRole + WorkerSnapshot
│   │   ├── target.py               # Target + WorkerSet + TargetUiState + compute_ui_state()
│   │   └── health.py               # TargetHealth + aggregate_target_health()
│   ├── fanout/                     # PHASE 5 — supervisor + ffmpeg workers + redaction + bus
│   │   ├── __init__.py
│   │   ├── worker.py               # Worker Protocol + WorkerSpec + WorkerEvent + payloads
│   │   ├── process.py              # SpawnedProcess + ProcessSpawner + AsyncioProcessSpawner
│   │   ├── redaction.py            # RedactionSink (line-buffered byte sanitizer)
│   │   ├── progress_parser.py      # ProgressParser + ProgressFrame + ProgressParseError
│   │   ├── backoff.py              # compute_backoff()
│   │   ├── ffmpeg_args.py          # FFMPEG_BASE_ARGS + build_argv()
│   │   ├── ffmpeg_urls.py          # compose_out_url() (per-platform; scheme guard)
│   │   ├── event_bus.py            # EventBus + BusEvent + payload dataclasses
│   │   ├── ffmpeg_worker.py        # FFmpegWorker (v1 Worker impl; lifecycle + breaker + stall)
│   │   ├── supervisor.py           # Supervisor (top-level orchestrator + WorkerFactory Protocol)
│   │   └── _testing.py             # PHASE 6 — FakeSupervisor (public test surface)
│   ├── api/                        # PHASE 6 — HTTP/WS surface (+ Phase 9 additions)
│   │   ├── __init__.py
│   │   ├── errors.py               # ErrorCode StrEnum + install_exception_handlers
│   │   ├── schemas.py              # Pydantic v2 request/response models
│   │   ├── middleware.py           # LockedModeMiddleware
│   │   ├── deps.py                 # SupervisorDep / EventBusDep / SettingsDep
│   │   ├── auth.py                 # auth_router + unlock_router
│   │   ├── targets.py              # CRUD + credential set/clear + reset-worker
│   │   │                           #   + PHASE 9 reveal-credential
│   │   ├── run.py                  # async 202 start/stop + state read
│   │   │                           #   + PHASE 9 rotation_lock gate
│   │   ├── settings_api.py         # GET/PATCH + rotate-ingest-key
│   │   │                           #   + PHASE 9 reveal-ingest-key
│   │   ├── api_tokens_api.py       # list / create / revoke
│   │   ├── sessions_history.py     # GET /api/sessions (SQL cursor)
│   │   ├── internal_rtmp.py        # /publish + /publish_done (loopback-only)
│   │   ├── security_api.py         # PHASE 9 — rotate-passphrase + GET/DELETE
│   │   │                           #   /api/security/sessions + GET /api/about
│   │   ├── ws.py                   # WebSocket + ws_broadcaster + WsRegistry
│   │   └── health.py               # /livez /readyz /healthz
│   └── main.py                     # PHASE 6 — create_app + lifespan + uvicorn
│                                   #   + PHASE 9: app.state.started_at +
│                                   #   app_started audit + .kdf_salt.previous
│                                   #   24-h cleanup task
│                                   #   + PHASE 10: _notify_s6_ready() (write+close
│                                   #   fd 3) called after supervisor.wait_ready;
│                                   #   workers=1 explicit in uvicorn.run()
│   # PHASE 10 edits also in app/config.py — healthz_check_nginx default flipped
│   # to True (production-correct); 4 conftests + 1 test fixture opt out.
├── tests/                          # 636 tests, all green
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_logging_setup.py
│   ├── crypto/{test_kdf,test_aead,test_passwords}.py
│   ├── db/{conftest,test_engine,test_schema_init,test_repositories}.py
│   ├── auth/{conftest,test_key_material,test_sessions,test_api_tokens,
│   │         test_rate_limit,test_reprompts,test_deps}.py
│   ├── domain/{test_errors,test_credential_policy,test_target_types,
│   │           test_run_state,test_worker_state,test_circuit_breaker,
│   │           test_target,test_health,test_converters,
│   │           test_no_outward_imports}.py
│   ├── fanout/{conftest,_fakes,test_no_inward_imports,
│   │           test_redaction,test_backoff,test_progress_parser,
│   │           test_ffmpeg_args,test_ffmpeg_urls,test_event_bus,
│   │           test_ffmpeg_worker,test_supervisor}.py
│   └── api/                         # PHASE 6/9 — 11 test files
│       ├── __init__.py
│       ├── conftest.py              # _make_test_app, auth_client fixture
│       ├── test_errors.py           # frozen ErrorCode contract (Phase 9 +5 codes)
│       ├── test_middleware.py       # allowlist helpers + parametrized real-router probes
│       ├── test_auth.py             # login / logout / unlock / reprompt / change-password
│       ├── test_targets.py          # CRUD + credential set/clear + reset-worker
│       ├── test_run.py              # 202 start/stop + state
│       ├── test_internal_rtmp.py    # nginx webhooks + loopback rejection
│       ├── test_ws.py               # WS auth + state.full + locked-mode close
│       ├── test_health.py           # /livez /readyz /healthz
│       ├── test_settings_and_tokens.py
│       └── test_phase9_endpoints.py # PHASE 9 — 20 tests covering 6 new endpoints
│                                    #   incl. rotate-passphrase atomicity injection
├── web/                            # PHASES 7/8/9 LANDED — see per-phase "what landed"
│   ├── package.json                # frontend deps (React 19, Vite, Tailwind v4, TanStack Query, Phosphor)
│   │                               #   + Phase 9: @radix-ui/react-popover,
│   │                               #     @radix-ui/react-slider, @hookform/resolvers
│   ├── tsconfig*.json, vite.config.ts, eslint.config.js (flat config), index.html
│   ├── scripts/check-bundle-size.mjs  # postbuild guard — fails > 256 KB gzipped total
│   └── src/
│       ├── main.tsx, App.tsx, router.tsx (Phase 9 mounts AuthRepromptHost)
│       ├── messages.ts, vite-env.d.ts
│       ├── theme/{tokens.css, ThemeManager.ts, ThemeToggle.tsx}
│       │                           #   + Phase 9: --shadow-popover token
│       ├── lib/                    # api, errors, eventBus, queryClient, safeNext,
│       │   │                       #   useReducedMotion, ws, cn, wsReducer (+ test)
│       │   │                       #   + Phase 9: queryKeys, formErrors, targetTypeSpecs
│       │   └── schemas/            # _shared, auth, health, run, targets, settings, wsEnvelopes
│       │                           #   + Phase 9: apiTokens, sessions, about
│       ├── hooks/                  # useRunState, useTargets, useWsConnectionStatus [Phase 8]
│       │                           #   + Phase 9: useSettings, useTargetsAdmin,
│       │                           #     useApiTokens, useHttpSessions, useRunHistory,
│       │                           #     useAbout, useAuthReprompt
│       ├── components/             # Phase 7 primitives + Phase 8 Dashboard chrome
│       │   │                       #   + Phase 9 NEW: SecretField, CopyToClipboard,
│       │   │                       #     DestructiveConfirm, TypeToConfirmDialog,
│       │   │                       #     OneTimeRevealBanner, Slider, AuthRepromptHost
│       │   └── settings/           # Phase 9: SettingsSidebar, SettingsSection
│       └── pages/                  # Login, Unlock, Dashboard, NotFound
│           └── settings/           # PHASE 9 NEW (one lazy chunk):
│                                   #   index.tsx, SettingsShell.tsx, GeneralTab,
│                                   #   SecurityTab, SessionsTab, AboutTab +
│                                   #   targets/{PersistentTargetTab, TwitchTab,
│                                   #   YouTubeTab, KickTab, VKTab, CustomTab}
├── docs/
│   ├── SESSION_HANDOFF.md          # YOU ARE HERE
│   ├── CODE_PLAN.md                # phased implementation plan
│   ├── prompts/                    # 4 agency-agent persona prompts (cached)
│   ├── architecture/
│   │   ├── README.md               # ADR index + design-review log
│   │   ├── system-overview.md      # C4, domain, data flow, failure modes
│   │   ├── platforms-reference.md  # verified RTMP endpoints (Twitch/YT/Kick/VK)
│   │   ├── design-review-2026-05-15.md  # full review log: findings → decisions
│   │   ├── ADR-0001-backend-stack.md
│   │   ├── ADR-0002-frontend-stack.md
│   │   ├── ADR-0003-rtmp-ingest-and-fanout.md
│   │   ├── ADR-0004-docker-shape.md
│   │   ├── ADR-0005-auth-model.md
│   │   ├── ADR-0006-secret-encryption.md
│   │   ├── ADR-0007-persistence.md
│   │   ├── ADR-0008-worker-abstraction.md
│   │   ├── ADR-0009-credential-model.md
│   │   ├── ADR-0010-headless-restart.md
│   │   └── ADR-0011-health-liveness.md
│   ├── ui/
│   │   ├── ux-flows.md             # IA, flows, every screen
│   │   └── design-system.md        # tokens, ~22 components, motion, a11y
│   └── ops/                        # NOT YET CREATED — phase 12
└── docker/                         # PHASE 10 LANDED — 4-stage Dockerfile bundle
    ├── Dockerfile                  # frontend-builder + nginx-builder + backend-builder + runtime
    ├── nginx.conf                  # rtmp{} block + redacted log formats
    ├── entrypoint.sh               # 22 lines; exec /init
    ├── healthcheck.sh              # curl /livez
    ├── nginx-rtmp.sha              # pinned commit (arut/nginx-rtmp-module)
    ├── requirements.lock           # runtime Python deps (pip --no-deps)
    └── s6/                         # s6-overlay v3 service tree
        ├── init-data/{type,up,down}             # oneshot — chown /data
        ├── nginx/                               # longrun
        │   ├── {type,run,finish,notification-fd,down-signal,timeout-finish}
        │   └── dependencies.d/init-data         # marker
        ├── control-plane/                       # longrun
        │   ├── {type,run,finish,notification-fd,down-signal,timeout-finish}
        │   ├── dependencies.d/{nginx,init-data} # markers
        │   └── log/{type,run}                   # s6-log companion → /data/logs/control-plane/
        └── user/contents.d/{init-data,nginx,control-plane}  # bundle markers
```

### Local dev quickstart (assumes a fresh shell)

```powershell
# From the project root:
py -3.12 -m venv .venv                                   # one-time
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"    # one-time
.\.venv\Scripts\ruff.exe check app tests                 # lint
.\.venv\Scripts\ruff.exe format --check app tests        # format check
.\.venv\Scripts\mypy.exe --strict app tests              # typecheck
.\.venv\Scripts\pytest.exe -q                            # test
```

Expected total wall-clock for the four checks: ~6 seconds on a modern
laptop. Argon2id at 128 MiB dominates; one fixture amortizes the cost.

---

## Things I deliberately did NOT do (do not re-litigate without cause)

- **Did not use AskUserQuestion for design decisions.** Per Rule №3,
  decisions were locked via ADR + expert-agent review. If you find
  yourself wanting to ask the user "what stack?" / "what auth?" —
  the answer is already in an ADR. Read it.
- **Did not implement** rotating-HMAC ingest key (B-1 from Backend
  Architect review). The proposal was rejected with rationale in the
  design-review log and ADR-0003 amended section. Future-me: do not
  reopen this without a new threat-model argument.
- **Did not implement** stateless JWT sessions. ADR-0005 explains why.
- **Did not add** Postgres. ADR-0007 explains why.
- **Did not add** Alembic / migration chain. ADR-0007 + Rule №2.
- **Did not add** docker-compose as the primary deployment. ADR-0004
  explains why one image is the right surface for this user.
- **Did not bundle** transcoding. The user has NVENC; we passthrough.
  ADR-0003 + the system-overview's encoding-is-OBS's-job note.

---

## Quick reference — the five user rules in short form

| # | Rule                              | Translated                                                           |
| - | --------------------------------- | -------------------------------------------------------------------- |
| 1 | No quick-and-dirty fixes          | Crutchless or nothing, even if slow.                                 |
| 2 | No migration baggage              | Edit decisions in place; greenfield from day one.                    |
| 3 | Don't ask user — spawn agents     | Use agency-agent personas at `docs/prompts/` and synthesize.         |
| 4 | Post-coding review agents         | `feature-dev:code-reviewer` after every meaningful coding chunk.     |
| 5 | Post-shipping audit               | After ship, verify claimed fixes / invariants / tests actually landed. |

---

## Open questions logged for future work

These are noted but not blocking. See the design-review log for
provenance.

- **Backup/restore CLI** (ADR-0006 amended): the procedure is
  documented in prose; the CLI subcommand was NOT bundled into Phase 6
  (HTTP-only) and now belongs squarely with Phase 11 (Ops docs +
  release-checklist).
- **`BaseHTTPMiddleware` → pure ASGI middleware** for
  `LockedModeMiddleware` (security review M-2): non-blocking
  robustness; convert when adding the next middleware (Phase 11
  Cache-Control on the SPA index.html is the natural moment).
- **Self-hosted Inter Variable + JetBrains Mono Variable** (Phase 7
  follow-up): the design-system §2 woff2 bundling was deferred from
  Phase 7. Platform fonts fill in via `system-ui` / `ui-monospace`. Land
  the binaries + `@font-face` declarations before Phase 9 surfaces the
  credential-display widgets where the monospace is most visible.
- **RTMPS from OBS** (ADR-0003 amended): the future-work path to
  encrypt OBS → server traffic. Requires either `nginx-rtmp-module`
  fork with TLS or an external stream proxy. Deferred.
- **TPM/keyring sealed passphrase** (ADR-0010): v2 mode reserved
  (`RESTREAM_PASSPHRASE_SOURCE=sealed`). Platform-specific code; out
  of scope for v1.
- **OAuth credentials** (ADR-0009): the `REFRESH_TOKEN` lifetime is
  declared but not implemented. Adds when the first such target type
  lands.
- **WebSocket bearer-token auth** (Phase 6 design memo §Q4): cookie-only
  on WS for v1; revisit if a scripted/headless consumer wants WS.

---

## Phase 9 — what landed (2026-05-16)

The entire Settings surface: 6 new backend endpoints (one of them —
`rotate-passphrase` — re-wraps every stored credential under a new
KEK + revokes all sessions + hard-deletes all api_tokens in one
atomic transaction); 6 new design-system components; the
`AuthRepromptHost` singleton that owns the `useAuthReprompt()`
contract; SettingsShell + 9 tabs (General, Security, Sessions,
About, Targets/{Twitch, YouTube, Kick, VK, Custom}).

Locked design at `docs/architecture/phase-9-design-memo.md`
(synthesis of 4 agency-agent passes — UX Architect, UI Designer,
Software Architect, Backend Architect — per Rule №3).

### Backend additions

```
app/
├── api/security_api.py             # NEW — 350 lines:
│                                   #   POST /api/security/rotate-passphrase
│                                   #   GET  /api/security/sessions
│                                   #   DELETE /api/security/sessions/{id_fingerprint}
│                                   #   GET  /api/about
├── api/settings_api.py             # + POST /api/settings/reveal-ingest-key
├── api/targets.py                  # + POST /api/targets/{id}/reveal-credential
├── api/run.py                      # start_run gated on AuthState.rotation_lock
├── api/errors.py                   # + 5 codes:
│                                   #     credential_not_found, new_passphrase_too_weak,
│                                   #     same_passphrase, run_active, session_not_found
├── api/schemas.py                  # + RevealCredentialResponse,
│                                   #   IngestKeyRevealResponse,
│                                   #   RotatePassphraseRequest (SecretStr fields),
│                                   #   HttpSessionView, AboutView
├── auth/reprompts.py               # + REVEAL_INGEST_KEY scope
├── auth/key_material.py            # + verify_passphrase() (async, Argon2id +
│                                   #   secrets.compare_digest on all 3 subkeys)
│                                   # + rotate_in_place() (atomic in-process swap)
├── auth/deps.py                    # + AuthState.rotation_lock: asyncio.Lock
├── repositories/credentials.py     # + list_all_for_rewrap() + rewrap()
├── repositories/sessions.py        # + list_active_for_user() + delete_all()
├── repositories/api_tokens.py      # + delete_all() (HARD delete on rotate)
├── repositories/audit_log.py       # + list_app_starts()
└── main.py                         # + app.state.started_at capture
                                    # + app_started audit on lifespan startup
                                    # + _kdf_salt_previous_cleanup_loop (24h GC)
                                    # + mount security_router + about_router
```

### Frontend additions

```
web/src/
├── pages/settings/                 # NEW — 11 files
│   ├── index.tsx                   # lazy entry; one chunk for /settings/*
│   ├── SettingsShell.tsx           # 240px sidebar + main; page-scoped status region
│   ├── GeneralTab.tsx              # ingest URL/key + idle timeout + log retention
│   ├── SecurityTab.tsx             # change-pwd + rotate-passphrase + API tokens +
│   │                               #   HTTP sessions + Sign-out-everywhere
│   ├── SessionsTab.tsx             # run history via useInfiniteQuery
│   ├── AboutTab.tsx                # version + uptime ticker + last reboots
│   └── targets/{TwitchTab,YouTubeTab,KickTab,VKTab,CustomTab,PersistentTargetTab}.tsx
├── components/                     # NEW — 8 design-system components
│   ├── SecretField.tsx             # variants masked|entry|paste_only with 60s countdown
│   ├── CopyToClipboard.tsx         # state machine idle/copying/copied/failed
│   ├── DestructiveConfirm.tsx      # inline Radix Popover popconfirm + 8s inactivity
│   ├── TypeToConfirmDialog.tsx     # Radix Dialog + dialog-scoped live region
│   ├── OneTimeRevealBanner.tsx     # focus on CopyToClipboard on mount
│   ├── Slider.tsx                  # Radix Slider, tabular-nums display
│   ├── AuthRepromptHost.tsx        # singleton context provider + dialog body
│   └── settings/{SettingsSidebar,SettingsSection}.tsx
├── hooks/                          # NEW — 7 hooks
│   ├── useSettings | useTargetsAdmin | useApiTokens
│   ├── useHttpSessions | useRunHistory | useAbout
│   └── useAuthReprompt              # (scope) => Promise<grantId>
└── lib/                            # NEW — 4 files + 4 extensions
    ├── queryKeys.ts                # central QueryKey constants
    ├── formErrors.ts               # ApiError → react-hook-form mapping
    ├── targetTypeSpecs.ts          # frontend mirror of TARGET_TYPE_SPECS
    └── schemas/{apiTokens,sessions,about}.ts
                + extended {auth,settings,targets,errors}.ts

DELETED per Rule №2:
  web/src/pages/SettingsPlaceholder.tsx
```

Other touches:
- `web/src/router.tsx`: mounts `<AuthRepromptHost>` inside
  `<RequireAuth>` (sibling of `<StatusStreamHost>` + `<AppShell>`);
  lazy-loads `import("@/pages/settings")` as ONE chunk.
- `web/src/components/AppShell.tsx`: account-menu items now `<Link
  to="/settings/security">` (was placeholder).
- `web/src/theme/tokens.css`: + `--shadow-popover` token.
- `web/src/messages.ts`: + namespaces `settings`, `general`,
  `security`, `sessionsTab`, `aboutTab`, `targetTab`, `secret`,
  `copy`, `confirm`, `reveal`, `slider`, `reprompt`. Deleted
  `settingsPlaceholder.*` and `appShell.comingInPhase9`.
- `web/package.json`: + `@radix-ui/react-popover`,
  `@radix-ui/react-slider`, `@hookform/resolvers`.

### Reviewer findings — all applied

1 HIGH + 5 MEDIUM from a parallel code-reviewer + security-reviewer
pass:

- **H-1** — `RotatePassphraseRequest.{old,new}_passphrase` were bare
  `str`; a 422 echo would surface the master passphrase plaintext.
  Fix: `SecretStr` + `.get_secret_value()` at the three call sites
  in `security_api.py`; same-passphrase compare via
  `secrets.compare_digest` on the unwrapped bytes.
- **M-1** — `AuthRepromptHost` rate-limit countdown decremented past
  `0` and never reset to `null` — the form re-rendered but stayed
  blocked. Fix: `setRetryAfter((s) => (s === null || s <= 1 ? null
  : s - 1))`.
- **M-2** — `test_rotate_passphrase_happy_path_rewraps_credentials`
  queried `list_active_for_user` with `session_token_mac_key.hex()`
  as the user_id — trivially empty. Fix: look up `admin.id` and
  assert `len(active) == 0`.
- **M-3** — `ChangePasswordSection` matched `err.status === 401`
  which catches both wrong-password (`invalid_credentials`) AND
  expired-session (`unauthorized`). Fix: match `err.code ===
  "invalid_credentials"` for the inline-error branch; route other
  401s through `queryClient.clear() + navigate("/login")`.
- **M-4** — `_kdf_salt_previous_cleanup_loop` used a blanket
  `contextlib.suppress(Exception)` swallowing `PermissionError` and
  audit failures silently. Fix: narrow to `FileNotFoundError`;
  emit `_logger.warning("kdf_salt_previous_cleanup_failed")` on
  other `OSError`; separate try/except around the audit append with
  its own `_logger.exception("kdf_salt_previous_cleanup_audit_failed")`.
- **M-5** — `StreamKeySection` + `GeneralTab` retained the
  revealed plaintext in React state after unmount (the React fiber
  held the string for the GC window). Fix: cleanup effect
  `useEffect(() => () => setRevealed(null), [])` on both.

### Rule №5 audit verdict — CLEAN (24/24)

Verified post-shipping: every reviewer finding lives in the diff;
every Phase 9 invariant (single AuthRepromptHost mount, no plaintext
in TanStack, queryClient.clear + nav-to-/login on rotate/change/
logout-all, silenceGlobalErrors on every Settings mutation, 5 new
error codes + 1 new scope synced across backend and frontend, bundle
under budget, SettingsPlaceholder.tsx deleted, 6 endpoints wired
into both prod and test app factories) holds. Test count delta:
616 → 636 (+20).

### Concrete invariants Phase 10+ must respect

1. Single `<AuthRepromptHost>` mounted exactly once inside
   `<RequireAuth>`, sibling of `<StatusStreamHost>` + `<AppShell>`.
2. Every destructive mutation calls `await reprompt(scope)` BEFORE
   firing.
3. The reprompt hook never caches grants across calls.
4. Plaintext credentials (stream keys, ingest key, API tokens,
   passphrases) NEVER touch the TanStack cache.
5. `queryClient.clear()` + `navigate("/login")` on
   rotate-passphrase / change-password / logout-all success.
6. Server error envelopes drive UI; client-side errors never
   masquerade as server errors.
7. `<RunStateBadge>`'s single `aria-live` for run-state stays the
   only owner of that concern. Settings owns ONE page-scoped
   status region for form-status announcements.
8. `silenceGlobalErrors: true` on every Settings mutation AND the
   reprompt POST.
9. Cache invalidation per phase-9-design-memo §O.
10. `useInfiniteQuery` for cursor-paginated endpoints
    (`/api/sessions`).
11. VK target form uses the SAME `PUT /api/targets/{id}/credential`
    endpoint as other types; the supervisor's `lifetime_for(type)`
    enforces per_session.
12. ONE route per target type. `/settings` redirects to
    `/settings/general`; unknown sub-paths under `/settings/*`
    redirect to General.
13. Phosphor-only icons; `messages.ts::t()` for every user-facing
    string.
14. No new localStorage keys. Settings live server-side.
15. No barrel files.
16. ONE lazy chunk for `/settings/*` (not per-tab, not per-form).
17. rotate-passphrase requires TWO knowledge proofs — admin password
    (via reprompt grant) AND master passphrase (in form body). The
    `KeyMaterial.verify_passphrase` constant-time check is the
    second gate; the 1.0 s wall-clock floor wraps the whole handler.
18. The `AuthState.rotation_lock` is process-wide. `start_run`
    consults it and 409s with RUN_ACTIVE if held; rotate-passphrase
    re-checks RunState INSIDE the lock to honor a concurrent
    start_run that won the race.
19. Backend audit log: every reveal/rotate/revoke emits an entry
    with `last4` only (never plaintext) — `credential_revealed`,
    `ingest_key_revealed`, `passphrase_rotated`,
    `session_revoked`, `app_started`,
    `kdf_salt_previous_cleared`.
20. The `.kdf_salt.previous` 24-h GC task survives process restart
    by re-checking mtime against the grace window on every tick.

### Open follow-ups (NOT blockers; Phase 10+ backlog)

1. **First-run-complete auto-flip.** Backend currently has
   `SettingsView.first_run_complete: bool` but no flip handler.
   Phase 8 uses localStorage as the source of truth on the
   Dashboard hint. Auto-flip in the supervisor on first successful
   `live` transition is a one-line change; deferred so the Dashboard
   UX continues to work unchanged.
2. **YouTube backup-ingest** toggle. ux-flows mentions a "Use
   backup ingest" switch; the worker model doesn't yet express
   "one target, two workers". Phase 10 ADR + UI deferred.
3. **AuthReprompt grant-expired auto-retry.** Design memo §C said
   one auto-retry on `REPROMPT_INVALID_OR_EXPIRED`; current
   implementation surfaces the error and lets the user re-click.
   Adding the one-shot retry is a small wrapper around
   `useMutation::onError`.
4. **HTTP sessions revoke reprompt.** Currently matches `logout-all`
   posture (no reprompt). If we ever revoke OTHER users' sessions
   (multi-admin future), this needs `REVOKE_SESSION` scope.

---

## Phase 10 — what landed (2026-05-16)

The container packaging. Everything Phases 1–9 produced is now
deliverable as one Docker image. No new tests; the phase is pure
infrastructure. Backend pipeline stayed at **636 tests green** with
ruff + ruff format + mypy --strict clean throughout.

### Files created

```
docker/Dockerfile                                 # 4-stage, multi-arch-ready
docker/nginx.conf                                 # per design memo §J
docker/entrypoint.sh                              # 22 lines; one fail-fast + exec /init
docker/healthcheck.sh                             # curl /livez
docker/nginx-rtmp.sha                             # arut/nginx-rtmp-module @ 9879e1d...
docker/requirements.lock                          # hand-curated runtime deps

docker/s6/init-data/{type,up,down}                # oneshot — chown /data
docker/s6/nginx/{type,run,finish,notification-fd,down-signal,timeout-finish}
docker/s6/nginx/dependencies.d/init-data
docker/s6/control-plane/{type,run,finish,notification-fd,down-signal,timeout-finish}
docker/s6/control-plane/dependencies.d/{nginx,init-data}
docker/s6/control-plane/log/{type,run}            # s6-log → /data/logs/control-plane/
docker/s6/user/contents.d/{init-data,nginx,control-plane}

docs/architecture/phase-10-design-memo.md         # synthesis of 2 agency-agent passes,
                                                  # 60 invariants I-A1..I-Q2
```

### Files modified

- `app/main.py` — added `_notify_s6_ready()` (writes `\n` to fd 3,
  closes it per reviewer C1; swallows OSError outside s6) called
  inside the lifespan AFTER `await supervisor.wait_ready(...)` and
  BEFORE `yield`. Added `workers=1` explicit kwarg to `uvicorn.run()`
  with a comment explaining why (load-bearing single-process
  invariant per ADR-0001 + design memo §M). Added
  `S6_NOTIFICATION_FD: Final[int] = 3` constant + `import os`.
- `app/config.py` — flipped `healthz_check_nginx` default from
  `False` to `True`. Production-correct: when the s6 bundle ships
  nginx in the same container, `/readyz` MUST surface nginx
  failures. Updated the docstring to point at the test opt-out path.
- `tests/api/conftest.py` — added `"healthz_check_nginx": False` to
  `_build_settings`'s base dict.
- `tests/auth/conftest.py` — same opt-out in `build_settings`.
- `tests/db/conftest.py` — same opt-out in `build_settings`.
- `tests/fanout/conftest.py` — same opt-out in `_build_settings`.
- `tests/api/test_phase9_endpoints.py::real_unlocked_app` fixture —
  added `healthz_check_nginx=False` to the inline `AppSettings(...)`
  construction (matches the conftest pattern).
- `.dockerignore` — refreshed allowlist; added `web/tsconfig.app.json`,
  `web/eslint.config.js`, `web/scripts/`; clarified the implicit-deny
  contract per I-B2.

### Design memo (60 invariants locked)

Synthesis of Software Architect + Backend Architect agency-agent
passes. Sections A–Q cover:

- **A** — multi-stage build topology + pinning policy
- **B** — image-size discipline (~383 MB budget vs 400 MB target)
- **C** — s6 service topology + `init-data` oneshot + `notification-fd`
  contract regardless of unlock state
- **D** — SIGTERM choreography (15s lifespan + 20s s6 + `docker stop -t 30`)
- **E** — passphrase modes from one image (no entrypoint branch)
- **F** — uid 10001 + `/data` chown via oneshot (not entrypoint)
- **G** — `CAP_NET_BIND_SERVICE` pre-applied for future :443 panel
- **H** — HEALTHCHECK probes `/livez` (NOT `/readyz` — paste-mode wedge)
- **I** — log redaction via `map`-based `$safe_uri` + `error_log warn`
- **J** — authoritative `application live` block (matches `app/api/internal_rtmp.py`)
- **K** — entrypoint ≤25 lines; one fail-fast gate; `exec /init`
- **L** — multi-arch readiness (`--platform=$BUILDPLATFORM` on builders)
- **M** — `workers=1` LOAD-BEARING + `_notify_s6_ready()` wiring
- **N** — structlog JSON to stdout AND `/data/logs/control-plane/` via s6-log
- **O** — boot-failure semantics ("s6 deps are 'started', not 'running'")
- **P** — reversibility audit (10 sticky items)
- **Q** — production-default tweaks (`healthz_check_nginx` + `workers=1`)
- **R** — extended acceptance criteria

7 DEFER-OR-AMEND items surfaced: DA-1 (ADR-0011 s6-ready vs `/readyz`
contract), DA-2 (nginx-rtmp fork choice — `arut` picked), DA-3
(ADR-0004 `/data/logs/` directory layout), DA-4 (`docker stop -t 30`
contract in ADR-0004 + README), DA-5 (pip `--require-hashes`
deferred to Phase 11), DA-6 (tarball SHA-256 verification — partial
mitigation shipped, full population in Phase 11), DA-7 (ADR-0010
`docker inspect` env exposure).

### Reviewer findings — all applied

**code-reviewer** (2 criticals + 2 importants + 1 medium):
- **C1**: `_notify_s6_ready()` wrote fd 3 but never closed it →
  leaked into ffmpeg children. Fixed: added `os.close(S6_NOTIFICATION_FD)`
  after the write.
- **C2**: I-E1 invariant text contradicted the entrypoint (which
  legitimately tests the env var's emptiness). Fixed: reworded I-E1
  to say "never EMITS the VALUE" (NAME-only references permitted in
  the fail-fast gate).
- **I3**: `nginx/run` + `control-plane/run` + `control-plane/log/run`
  missing `set -eu` → exec failures continued silently. Fixed in
  all three.
- **I4**: `tests/auth/conftest.py` + `tests/db/conftest.py` +
  `tests/fanout/conftest.py` didn't opt out of
  `healthz_check_nginx=True` after the default flip — latent test
  trap. Fixed in all three.
- **M5**: `nginx/run` printf'd to fd 3 without closing → defense-in-depth
  gap. Fixed: added `exec 3>&-` immediately after the notification.

**security-reviewer** (2 highs + 4 mediums + 5 lows):
- **H1**: No SHA-256 integrity check on nginx / nginx-rtmp-module /
  s6-overlay tarballs. Fixed (partial): added 5 `ARG <X>_SHA256=""`
  slots + conditional `sha256sum -c -` gates in the Dockerfile.
  Empty ARG = trust-on-first-build with stderr warning; populated
  ARG = enforced verification. Phase 11 CI populates.
- **H2**: `pip install --no-deps` lacks `--require-hashes`. DEFERRED
  to Phase 11 (requires PyPI resolver access for hash generation;
  bundled with the Renovate/Dependabot dep-bump tooling).
  Documented as DA-5 in the design memo.
- **M1**: Design memo §F claimed nginx master ran as `restream` —
  wrong (master always inherits the uid that exec'd it; `user
  restream;` only affects workers). Fixed: corrected §F first-boot
  sequence; corrected I-C4 invariant ("no APPLICATION code runs as
  root"); added explanatory note on the `setcap` being currently
  unused but pre-applied.
- **M2**: `init-data/up` did `chown -R` (would follow symlinks if
  `/data` itself were a symlink). Fixed: added `[ -L /data ]` guard
  + switched to `chown -hR`.
- **M3**: `chown` ran unconditionally on every restart, stomping
  operator-owned files dropped via `docker cp`. Fixed: gated on
  `stat -c '%u' /data != 10001`.
- **M4**: `umask 027` set in entrypoint but not re-set in run
  scripts. Fixed: added `umask 027` to nginx/run + control-plane/run.
- **L3**: `setcap` had no build-time tripwire. Fixed: added
  `getcap /usr/sbin/nginx | grep -q cap_net_bind_service` after the
  `setcap` line.
- **L5**: `with-contenv` (which forwards RESTREAM_* env including
  the passphrase) used unnecessarily on services that don't need
  Python env. Fixed: dropped `with-contenv` from nginx/run +
  init-data/up + control-plane/log/run; kept only on
  control-plane/run where python needs the env.
- L1/L2/L4/I1-I8: informational / no action needed.

### Rule №5 audit — CLEAN

13/13 claimed fixes verified by post-implementation grep (see the
audit Bash output during the work). Specifically:
- C1: `os.close(S6_NOTIFICATION_FD)` at `app/main.py:422` ✓
- I3: `set -eu` in 5 shell scripts ✓
- I4: `healthz_check_nginx=False` in 5 test files ✓
- H1: 5 `ARG *_SHA256=""` + 3 conditional sha256sum gates ✓
- M1: "nginx MASTER stays as root by design" in memo ✓
- M2: `chown -hR` + `[ -L /data ]` guard ✓
- M3: `current_uid="$(stat -c '%u' /data)"` idempotency ✓
- M4: `umask 027` in 3 places ✓
- M5: `exec 3>&-` in `nginx/run` ✓
- L3: `getcap | grep` tripwire ✓
- L5: only `control-plane/run` uses `with-contenv` ✓
- Memo I-E1 rewording ✓
- Memo I-K1 bump 20→25 ✓

Plus: 636/636 tests pass; ruff + ruff format + mypy --strict clean.

### Twenty concrete invariants Phase 11 + 12 must respect

1. `/livez` is the HEALTHCHECK target. NEVER use `/readyz` here —
   paste-mode locked state would mark the container unhealthy
   forever.
2. `docker stop -t 30` is load-bearing operator guidance. Docs
   (README + compose.yaml + ops/deployment.md) MUST repeat it.
3. `workers=1` in uvicorn.run is single-process invariant from
   ADR-0001. Multi-worker breaks KeyMaterial, supervisor,
   rate-limiter, WS broadcast.
4. `_notify_s6_ready()` fires REGARDLESS of unlock state. The s6
   "ready" contract is `/livez` semantics, not `/readyz`.
5. fd 3 is **written + closed** in one operation. The close prevents
   the fd from leaking into ffmpeg children.
6. `control-plane → nginx` dependency means control-plane STARTS
   after nginx but STOPS BEFORE nginx (reverse-topological s6
   shutdown). Workers drain before nginx tears down.
7. s6 deps are "started", not "running". A nginx crash does NOT
   cascade-kill control-plane. `/readyz` honestly reports
   `rtmp_ingest.ok=false`.
8. `on_publish` URL `http://127.0.0.1:8000/internal/rtmp/publish` is
   coupled to `app/config.py::bind_port`. Change both together.
9. `application live` block max_streams=1 (one publish at a time);
   play_restart=off (control plane owns reconnect).
10. nginx ACL: `allow play 127.0.0.1; deny play all;`. NEVER relax.
11. nginx writes its OWN logs (`/data/logs/nginx-{access,error}.log`).
    s6-log writes control-plane's logs (`/data/logs/control-plane/`).
12. error_log level is `warn` — drops the info-level `name=<key>`
    publish lines that contain the ingest key.
13. uid 10001 is LOAD-BEARING for `/data` ownership. Changing it
    breaks every operator's pre-chowned host directory.
14. `/data` chown is idempotent (skips when top-level already 10001)
    AND symlink-safe (`-h` + `[ -L /data ]` guard).
15. `setcap cap_net_bind_service=+ep` on nginx is forward-looking
    (currently unused — 1935 + 8000 are >1024). The day we expose
    :443, it becomes load-bearing; nginx.conf `user restream;` must
    be removed in lockstep if we ever drop nginx master to non-root.
16. `--platform=$BUILDPLATFORM` on builders; runtime stage inherits
    `$TARGETPLATFORM`. No hardcoded arch strings in `RUN`.
17. `RESTREAM_PASSPHRASE_SOURCE=env` is the runtime ENV default; the
    fail-fast gate in entrypoint catches the missing-passphrase case
    BEFORE pydantic ValidationError.
18. `with-contenv` on `control-plane/run` ONLY (python needs the
    env). nginx + s6-log + init-data have plain `/bin/sh` — narrows
    the env-forwarding blast radius.
19. SHA-256 verification gates fire when `*_SHA256` ARGs are populated.
    Phase 11 CI MUST populate them via `--build-arg`.
20. `pip install --no-deps -r requirements.lock` (no `--require-hashes`
    in Phase 10). Phase 11 CI replaces requirements.lock with a
    `pip-compile --generate-hashes` output and flips the flag.

### Open follow-ups (from Phase 9 — still open)

The same 4 items carried forward (none touched by Phase 10):

1. **first-run-complete auto-flip** in supervisor on first successful
   `live` transition. One-line change deferred so the Dashboard UX
   continues to work unchanged.
2. **YouTube backup-ingest** toggle — "one target, two workers"
   model change. Deferred to a future phase.
3. **AuthReprompt grant-expired auto-retry** — one-shot retry
   wrapper around `useMutation::onError`. Small.
4. **HTTP sessions revoke reprompt** — needs `REVOKE_SESSION` scope
   if we ever go multi-admin.

### Phase 11 entry conditions

Phase 11 (CI: GHA workflow + GHCR publish) inherits these load-bearing
constraints from Phase 10:

- The Dockerfile builds the same way amd64 + arm64. qemu nginx-rtmp
  build is ~5-10 min per arch (I-L4 risk register).
- Populate every `*_SHA256` build-arg in CI (DA-6).
- Replace `requirements.lock` with `pip-compile --generate-hashes`
  output and flip to `pip install --require-hashes` (DA-5).
- Add a `getcap /usr/sbin/nginx` smoke test in CI's post-build phase
  to catch any Dockerfile reorder that loses the cap.
- Verify image size ≤ 420 MB uncompressed (I-B3 alarm).
- Renovate / Dependabot config (DEFER-OR-AMEND-4) — weekly bump PRs
  against `docker/nginx-rtmp.sha`, nginx version, s6-overlay
  version, Python deps lockfile, base image digests.

---

## Glossary (load-bearing terms)

- **RunState** — system-wide. `offline | starting | armed | live |
  stopping | error`. Toggled by START / STOP and ingest events.
- **TargetEnabled** — per-target policy. `enabled | disabled`.
- **WorkerState** — per-Worker runtime. `idle | starting | running |
  reconnecting | failed_open | stopping`.
- **Target** — config object: URL, type, label, enabled flag. Owns
  1..N Workers.
- **Worker** — protocol; one outbound push to one URL. `FFmpegWorker`
  is the v1 impl.
- **Credential** — separate entity with `lifetime`: `persistent` /
  `per_session` / `refresh_token`. VK Live → `per_session`.
- **Ingest key** — the stable secret OBS puts in its stream-key field.
  Doubles as the RTMP stream name. Threat model in ADR-0003.
- **Master passphrase** — root of trust. Argon2id → root_key → HKDF
  derives `stream_key_kek` and `api_token_mac_key`.
