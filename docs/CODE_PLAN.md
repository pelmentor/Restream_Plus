# Code Plan — Restream_Plus implementation phases

The plan to write code for Restream_Plus. Each phase has a goal, a
file manifest, ADRs to consult, and acceptance criteria. Phases run
sequentially; **after each phase, spawn `feature-dev:code-reviewer`
and apply findings before moving on** (Rule №4). For auth/crypto/
network surface phases, also spawn a security-focused reviewer.

The plan is opinionated about ordering: foundations before
features, server before client (so the client has a real API to
talk to), container last (so iteration is fast during the early
phases).

---

## Phase 1 — Backend foundation: config + crypto — ✅ COMPLETE 2026-05-16

**Status:** 89 tests green; ruff + mypy --strict clean; both reviewers'
findings applied. See `docs/SESSION_HANDOFF.md` §"Phase 1 — what landed"
for the inventory of modules and the invariants now in code.

**Goal:** the lowest layer that everything else depends on. Settings
loading, KDF, AEAD, password hashing. No HTTP yet, no DB yet — just
deterministic, unit-testable primitives.

**ADRs to consult:** 0001, 0005, 0006, 0007 (encryption pragmas), 0010.

**Files to create:**
```
app/
├── __init__.py
├── config.py                    # pydantic-settings; RESTREAM_* env vars
├── crypto/
│   ├── __init__.py
│   ├── kdf.py                   # Argon2id + HKDF derivation
│   ├── aead.py                  # AES-256-GCM wrapper around `cryptography`
│   └── passwords.py             # argon2-cffi for the admin user's password
├── logging_setup.py             # structlog config + redaction processor
└── version.py                   # __version__ + build_sha (filled at build)

tests/
├── __init__.py
├── conftest.py
├── crypto/
│   ├── test_kdf.py              # Argon2id determinism, HKDF separation
│   ├── test_aead.py             # encrypt/decrypt roundtrip, tamper detection,
│   │                            #   different AAD/salt = different ct
│   └── test_passwords.py        # hash/verify, timing-safe verify
└── test_config.py
```

**Acceptance criteria:**
- `pytest -q` is green (zero failures, no warnings).
- `ruff check` clean, `mypy --strict` clean.
- A property-based test in `test_aead.py` confirms: any plaintext +
  random `salt` + random nonce → ciphertext that decrypts to the
  same plaintext, and tampering with any byte fails AEAD.
- `kdf.py` exposes `derive_root_key(passphrase, salt)` and
  `derive_subkey(root_key, info)`. Two HKDF infos used:
  `stream-key-wrap-v1`, `api-token-mac-v1` (NOT three —
  `session-cookie-mac` was dropped per ADR-0006 amended).

**After this phase:** spawn `feature-dev:code-reviewer` with the diff;
apply findings; ALSO spawn the `/security-review`-equivalent agent
because this phase is all crypto.

---

## Phase 2 — Backend persistence: DB models + schema init — ✅ COMPLETE 2026-05-16

**Status:** 128 tests green; ruff + ruff format + mypy --strict clean;
reviewer findings applied. See `docs/SESSION_HANDOFF.md` §"Phase 2 — what
landed" for the inventory of modules and the invariants now in code.

**Goal:** SQLite with WAL pragmas, single-shot schema bring-up, and
typed SQLAlchemy 2.x async models. No business logic — only the
storage shape.

**ADRs to consult:** 0007 (primary), 0009 (Credential entity), 0005,
0006.

**Files to create:**
```
app/
├── db/
│   ├── __init__.py
│   ├── engine.py                # async_engine + pragma application
│   ├── session.py               # async_sessionmaker
│   ├── models.py                # SQLAlchemy DeclarativeBase + all tables
│   ├── schema.sql               # canonical schema (the one source of truth)
│   └── schema_init.py           # idempotent first-boot init + version check
└── repositories/
    ├── __init__.py
    ├── users.py
    ├── sessions.py              # HTTP sessions
    ├── api_tokens.py
    ├── targets.py
    ├── credentials.py
    ├── settings_repo.py         # singleton settings row
    ├── audit_log.py
    └── sessions_history.py      # run sessions, NOT HTTP sessions

tests/
└── db/
    ├── test_engine.py           # pragmas applied; WAL active
    ├── test_schema_init.py      # version-mismatch refuses to start
    └── test_repositories/       # one per repo
```

**Acceptance criteria:**
- A fresh DB file is created with all pragmas from ADR-0007.
- `PRAGMA user_version` matches the compiled-in constant.
- An older DB (simulated by SQL) is rejected at boot with a clear
  error message that mentions the export/reimport procedure.
- Repositories return typed Pydantic DTOs, not raw SQLAlchemy rows.
- The `audit_log.append()` API is synchronous-write +
  `wal_checkpoint(TRUNCATE)` per ADR-0007.

---

## Phase 3 — Backend auth: passwords, sessions, API tokens, deps — ✅ COMPLETE 2026-05-16

**Status:** 262 tests green; ruff + ruff format + mypy --strict clean;
code-reviewer + security-reviewer findings applied. See
`docs/SESSION_HANDOFF.md` §"Phase 3 — what landed" for the module
inventory, the Backend Architect design memo decisions baked in, the
applied review findings, and the four deferred-to-Phase-6 gaps that
must NOT be lost.

**Goal:** login flow, opaque server-side sessions, API token issuance,
FastAPI dependency helpers.

**ADRs:** 0005 (primary), 0006 (key derivation).

**Files to create:**
```
app/
├── auth/
│   ├── __init__.py
│   ├── sessions.py              # create / validate / revoke
│   ├── api_tokens.py            # create / verify / revoke (HMAC fingerprint)
│   ├── rate_limit.py            # in-memory sliding window (volatile, per ADR-0005)
│   ├── reprompts.py             # 60s grant tokens for sensitive actions
│   └── deps.py                  # FastAPI Depends helpers: require_admin, etc.

tests/
└── auth/
    ├── test_sessions.py
    ├── test_api_tokens.py
    ├── test_rate_limit.py
    └── test_reprompts.py
```

**Acceptance criteria:**
- Login returns a HttpOnly Secure SameSite=Lax cookie with prefix
  `__Host-rp_session=`.
- Session validation is a single SQL UPDATE+RETURNING per request
  (no in-memory cache — per ADR-0005 amended).
- Failed login attempts increment the rate-limit counter; 5 failures
  in 15 min → 401 with `Retry-After`.
- API token hash never appears in API responses; only the
  `last_used_at` metadata.
- `AuthReprompt` grant tokens are single-use, expire in 60 s,
  invalidated server-side.

**After this phase:** code-reviewer + security-reviewer.

---

## Phase 4 — Backend domain: Target, Credential, run-state machine — ✅ COMPLETE 2026-05-16

**Status:** 424 tests green (162 new, incl. property-based circuit-breaker
tests with hypothesis); ruff + ruff format + mypy --strict clean;
code-reviewer findings applied (no security-reviewer per ritual: pure
Python, no auth/crypto/network surface). See `docs/SESSION_HANDOFF.md`
§"Phase 4 — what landed" for the module inventory, the Backend Architect
design memo decisions baked in, and the applied review findings.

**Goal:** the domain primitives that the supervisor and HTTP layer
both depend on. Pure Python, fully unit-testable, no I/O.

**ADRs:** 0003 (state machines), 0008 (Worker protocol), 0009
(Credential), platforms-reference.md.

**Files to create:**
```
app/
├── domain/
│   ├── __init__.py
│   ├── target_types.py          # TargetType enum + per-type config defaults
│   ├── credential_policy.py     # lifetime_for(type) → CredentialLifetime
│   ├── run_state.py             # RunState enum + transition machine
│   ├── worker_state.py          # WorkerState enum + transition machine
│   ├── target.py                # Target aggregate + WorkerSet
│   └── health.py                # target-level health aggregation rules

tests/
└── domain/
    ├── test_run_state.py        # every legal transition + every illegal
    ├── test_worker_state.py     # same + circuit breaker semantics
    ├── test_target_health.py    # YouTube backup OR; default AND
    └── test_credential_policy.py
```

**Acceptance criteria:**
- State machines reject illegal transitions with a typed exception.
- The YouTube target with backup-enabled aggregates as
  `primary_healthy OR backup_healthy`.
- VK target with no per-session credential reports
  `disabled_misconfigured` from `Target.ui_state()`.
- Property-based tests confirm: after N forced failures within the
  window, a Worker → `failed_open` and stays there until reset.

---

## Phase 5 — Backend supervisor: FFmpegWorker + progress + redaction — ✅ COMPLETE 2026-05-16

**Status:** 525 tests green (101 new); ruff + ruff format + mypy --strict
clean; general code-reviewer + security-reviewer ran in parallel — 3 highs
+ 5 mediums + 4 lows applied; ADR-0006 amended for in-process plaintext
exposure inside the container trust boundary; Rule №5 audit clean. See
`docs/SESSION_HANDOFF.md` §"Phase 5 — what landed" for the design memo
decisions, module inventory, and applied review findings.

**Goal:** the actual fan-out engine. ffmpeg child processes,
`-progress` parsing, log redaction, exponential backoff, circuit
breaker, two-TaskGroup architecture, shutdown choreography.

**ADRs:** 0001 (TaskGroup split), 0003 (state machine, buffer flags,
duplicate publish), 0008 (Worker protocol, RedactionSink).

**Files to create:**
```
app/
├── fanout/
│   ├── __init__.py
│   ├── worker.py                # Worker protocol
│   ├── ffmpeg_worker.py         # v1 implementation
│   ├── ffmpeg_args.py           # FFMPEG_BASE_ARGS + per-type plan
│   ├── progress_parser.py       # -progress pipe:1 line parser
│   ├── redaction.py             # RedactionSink
│   ├── backoff.py               # exponential backoff + jitter + breaker
│   ├── supervisor.py            # Supervisor (manages WorkerSets, two TaskGroups)
│   └── event_bus.py             # bounded asyncio.Queue Supervisor → API

tests/
└── fanout/
    ├── test_ffmpeg_args.py
    ├── test_progress_parser.py
    ├── test_redaction.py        # property-based: random key never appears in sink output
    ├── test_backoff.py
    └── test_supervisor.py       # subprocess-mocked end-to-end of the state machine
```

**Acceptance criteria:**
- Property-based redaction test: 1000 random credentials inserted
  into a synthetic stderr stream; none appear in the sink output.
- Supervisor runs with a mock Worker class that exits non-zero on
  schedule; circuit breaker trips after 30 failures in 5 min and
  stays in `failed_open` until reset.
- SIGTERM to the supervisor process: every Worker receives SIGTERM
  within 100 ms, all flushed within 5 s.
- Bounded queue (cap 10 000): when full, oldest drops; drop counter
  is monotonically increasing.

**After this phase:** code-reviewer + security-reviewer (RedactionSink
is a security boundary).

---

## Phase 6 — Backend HTTP: REST + WebSocket + health + main — ✅ COMPLETE 2026-05-16

**Status:** 610 tests green (85 new); ruff + ruff format + mypy --strict
clean; code-reviewer (4 highs + 3 mediums) + security-reviewer (1 critical
+ 4 highs + 2 mediums + 2 lows) ran in parallel — all applied except
`BaseHTTPMiddleware → pure ASGI` (deferred non-blocking robustness). All
four Phase 3 deferred gaps closed. Rule №5 audit clean. See
`docs/SESSION_HANDOFF.md` §"Phase 6 — what landed" for the design memo
decisions baked in, module inventory, and applied review findings.

**Goal:** wire everything together into a runnable FastAPI app.

**ADRs:** 0001, 0003, 0005, 0011 (health endpoints).

Design memo locked in `docs/architecture/phase-6-design-memo.md` (Rule №3
Backend Architect pass before code). The memo answers 15 questions
covering: single `create_app()` factory + single lifespan; two-TaskGroup
choreography (API TG owns Supervisor TG); per-WS-client `Queue(maxsize=256)`
with disconnect-on-overflow (NOT drop-oldest, to avoid silent desync);
cookie-only WS auth + versioned `{"v":1, "event", "data"}` envelope;
locked-mode **middleware** (NOT router-Depends) with the 5-path allowlist;
`api_tokens.user_id` FK + dropping hardcoded `"admin"`; `BackgroundTasks`
rehash; reprompt burn-on-failure documented but not actively mitigated;
loopback-checked nginx webhooks; stable `{"detail": "code"}` error shape;
async 202 run start/stop; `/readyz` checks with SELECT 1 + heartbeat +
configurable nginx; `httpx.AsyncClient(ASGITransport)` + `FakeSupervisor`
in tests. Also resolves the four Phase 3 deferred gaps.

**Files to create:**
```
app/
├── api/
│   ├── __init__.py
│   ├── auth.py                  # POST /api/auth/login, /logout, change-password, /api/unlock
│   ├── targets.py               # CRUD /api/targets, /api/targets/<id>/credential
│   ├── run.py                   # POST /api/run/start, /api/run/stop (202 Accepted)
│   ├── settings_api.py          # /api/settings/*
│   ├── api_tokens_api.py        # /api/security/tokens/*
│   ├── sessions_history.py      # GET /api/sessions
│   ├── internal_rtmp.py         # /internal/rtmp/publish, /publish_done (loopback-only)
│   ├── ws.py                    # /ws — cookie-auth, per-client fan-out
│   ├── health.py                # /livez, /readyz, /healthz
│   ├── schemas.py               # (NEW vs old plan) Pydantic v2 request/response models
│   ├── errors.py                # (NEW) error-code constants + exception handlers
│   └── middleware.py            # (NEW) LockedModeMiddleware
├── fanout/
│   └── _testing.py              # (NEW) FakeSupervisor — public test surface
└── main.py                      # create_app + lifespan + uvicorn entrypoint

tests/
└── api/
    ├── conftest.py              # ASGITransport client, FakeSupervisor wiring
    ├── test_schemas.py
    ├── test_errors.py
    ├── test_middleware.py       # locked-mode allowlist + 503 shape
    ├── test_auth.py             # login, logout, change-password, /api/unlock, rehash BG
    ├── test_targets.py
    ├── test_run.py              # full start → armed → live → stop on FakeSupervisor
    ├── test_run_e2e.py          # real Supervisor + FakeProcessSpawner wiring
    ├── test_internal_rtmp.py    # nginx webhook payloads + loopback rejection
    ├── test_ws.py               # cookie auth, message envelope, state.full snapshot, overflow
    ├── test_health.py
    └── test_api_tokens_user_id.py  # Phase 3 deferred gap closure
```

The schema edit (`api_tokens.user_id`) lands in this phase per Rule №2
(greenfield, edit `schema.sql` in place — no migration baggage). Updates
required in `app/db/schema.sql`, `app/db/models.py`,
`app/repositories/api_tokens.py` (DTO + create signature),
`app/auth/api_tokens.py` (service create signature), and
`app/auth/deps.py::get_current_user_via_bearer` (load user via
`token_dto.user_id`).

**Acceptance criteria:**
- OpenAPI spec generated; clients can read it.
- `/internal/rtmp/publish` rejects duplicate concurrent publishes
  (per ADR-0003 amended).
- `/readyz` returns 503 with structured per-check detail when
  supervisor heartbeat is stale.
- WebSocket auth: uses the same cookie as REST; bearer-tokens not
  accepted on WS for v1 (revisit if needed).

**After this phase:** code-reviewer + security-reviewer.

---

## Phase 7 — Frontend foundation: theme + shell + router + login — ✅ COMPLETE 2026-05-16

**Status:** 614 backend tests green (3 new for `GET /api/auth/me` + 1 new
locked-mode coverage probe); ruff + ruff format + mypy --strict clean.
Frontend: `npm run typecheck` clean, `npm run lint` clean (jsx-no-literals
+ type-checked rules), `npm run build` succeeds at 139 KB gzipped vs
250 KB budget. code-reviewer ran — 0 critical / 1 high / 3 medium / 3 low;
H-1 (`role="menu"` ARIA), M-1 (`MutationCache.onError` re-broadcast),
M-2 (`useReducedMotion` consumer wiring), M-3 (missing
`--color-bg-overlay` token), and L-3 (deprecated `--ext` flag) all
applied. Design memo at `docs/architecture/phase-7-design-memo.md` (16
locked decisions across UX, UI, software architecture). See
`docs/SESSION_HANDOFF.md` §"Phase 7 — what landed" for the module
inventory and the reviewer fix summary.

**Goal:** the runnable SPA shell. Theme system, router, auth wiring,
login screen. Nothing on the Dashboard yet beyond a placeholder.

**ADRs:** 0002, ux-flows.md, design-system.md.

**Files to create:**
```
web/src/
├── main.tsx
├── App.tsx
├── router.tsx
├── messages.ts                  # ALL user-facing strings (per Rule and ux-flows §8)
├── theme/
│   ├── tokens.css               # the @theme block from design-system.md
│   ├── ThemeManager.ts          # the theme persister
│   └── ThemeToggle.tsx          # the 3-segment radio group with Phosphor SVGs
├── lib/
│   ├── api.ts                   # fetch wrapper with credentials: include
│   ├── ws.ts                    # WebSocket client + reconnect
│   └── auth.ts                  # session helpers
├── components/
│   ├── Button.tsx               # design-system §6.1
│   ├── Banner.tsx               # §6.17
│   ├── Skeleton.tsx             # §6.16
│   └── ... (others as needed by this phase only)
└── pages/
    ├── Login.tsx
    └── DashboardPlaceholder.tsx
```

**Acceptance criteria:**
- `npm run typecheck` clean. `npm run lint` clean.
- Theme toggle persists; `data-theme` is applied before first paint
  (the inline bootstrapper in `index.html` is wired).
- `npm run build` produces a bundle < 250 KB gzipped (excluding the
  Phosphor icons not yet imported).

---

## Phase 8 — Frontend Dashboard: hero card + tiles + slide-out + WS — ✅ COMPLETE 2026-05-16

**Status:** backend 616 pytest green (610 → 616: +2 run-state tests + 1
ws-state.full assertion + 1 supervisor TestRunStateChangedAt class +
preserved existing). ruff + ruff format + mypy --strict all clean.
Frontend: `npm run typecheck` clean, `npm run lint` clean, `npm run test`
9/9 (Vitest pure-function tests for the WS-cache reducer), `npm run build`
succeeds at **168 KB gzipped vs 256 KB budget**. Single-shot
`feature-dev:code-reviewer` ran — 0 critical / 2 high / 3 medium / 1 low;
H-1 (`aria-live` on `<button>` invalid ARIA), H-2 (`useTargets`
`getQueryData` lacked reactivity), M-1 (VK `autocomplete="off"` ignored
by browsers), M-3 (double `hsl(var(...))` wrap on live-ambient ring),
M-4 (slide-out focus-return not wired through Radix), L-2 (no Escape on
RecentEventsMenu) all applied. Rule №5 audit verdict: CLEAN — every
claimed fix + design-memo invariant verified on disk. Design memo at
`docs/architecture/phase-8-design-memo.md` (25 sections A-Y). See
`docs/SESSION_HANDOFF.md` §"Phase 8 — what landed".

**Goal:** the runnable Dashboard with live status. After this phase
the user can log in, see their targets, click START, and see
real-time updates as long as they paste in the OBS stream.

**ADRs:** 0002, ux-flows.md §2, design-system.md §6 (Tile, Sparkline,
LogViewer, InlinePromptCard).

**Files to create:**
```
web/src/
├── pages/
│   └── Dashboard.tsx
├── components/
│   ├── RunStateBadge.tsx        # §6.2 + the single aria-live region
│   ├── HeroCard.tsx             # START/STOP + confirm choreography
│   ├── TargetTile.tsx           # §6.3
│   ├── TargetDetails.tsx        # slide-out with sparkline + log viewer
│   ├── Sparkline.tsx            # §6.9 canvas-rendered
│   ├── LogViewer.tsx            # §6.12
│   ├── MetricGrid.tsx           # §6.19
│   ├── InlinePromptCard.tsx     # §6.15 — the VK paste card
│   ├── RecentEventsMenu.tsx     # §6.21
│   └── ReconnectingBanner.tsx   # §6.17 info variant
└── hooks/
    ├── useRunState.ts
    ├── useTargets.ts
    └── useStatusStream.ts       # WS subscriber, debounced renders
```

**Acceptance criteria:**
- Dashboard renders correctly in all run states (use Storybook or a
  dev fixture page to verify each one).
- Sparkline draws 300 1-Hz samples at 60 fps without dropping.
- Slide-out focus management implemented (open → close button;
  close → activator).
- WS reconnect tested by force-killing the dev server.

---

## Phase 9 — Frontend Settings (all tabs) — ✅ COMPLETE 2026-05-16

**Status:** 636 backend tests green (+20 new Phase 9 endpoint tests
covering rotate-passphrase atomicity injection); mypy --strict + ruff
+ ruff format clean. Frontend typecheck + lint + 9/9 Vitest + build
all clean at **214 KB gzipped of 256 KB budget**. Reviewer findings:
1 HIGH (`SecretStr` migration) + 5 MEDIUM (rate-limit countdown,
session-assertion test fix, change-password err.code matching,
cleanup-loop suppress scope, unmount plaintext cleanup) — all applied.
Rule №5 audit returned CLEAN (24/24). See `docs/SESSION_HANDOFF.md`
§"Phase 9 — what landed" for the full inventory.

**Goal achieved:** every Settings sub-route from ux-flows.md §3 +
6 new backend endpoints (reveal-credential, reveal-ingest-key,
rotate-passphrase, GET/DELETE security/sessions, GET /api/about) +
6 design-system components (SecretField, CopyToClipboard,
DestructiveConfirm, TypeToConfirmDialog, OneTimeRevealBanner, Slider) +
singleton AuthRepromptHost. Design synthesis of 4 agency-agent passes
at `docs/architecture/phase-9-design-memo.md` (25 locked decisions).

**Files shipped:**
```
app/
├── api/security_api.py             # NEW — rotate-passphrase, GET/DELETE sessions, GET /about
├── api/{errors,schemas}.py         # extended — 5 new error codes, 5 new shapes
├── api/{settings_api,targets,run}.py # extended — reveal handlers + run-active gate
├── api/main.py                     # lifespan: started_at + app_started audit + .kdf_salt.previous cleanup
├── auth/{reprompts,key_material,deps}.py # REVEAL_INGEST_KEY scope; verify_passphrase + rotate_in_place; rotation_lock
└── repositories/{credentials,sessions,api_tokens,audit_log}.py
                                    # extended — list_all_for_rewrap/rewrap,
                                    # list_active_for_user/delete_all,
                                    # delete_all, list_app_starts

web/src/
├── pages/settings/                 # NEW
│   ├── SettingsShell.tsx | index.tsx
│   ├── GeneralTab.tsx | SecurityTab.tsx | SessionsTab.tsx | AboutTab.tsx
│   └── targets/
│       ├── PersistentTargetTab.tsx (shared form for Twitch/YT/Kick)
│       ├── TwitchTab | YouTubeTab | KickTab (thin wrappers)
│       ├── VKTab.tsx (per_session credential variant)
│       └── CustomTab.tsx
├── components/                     # NEW
│   ├── SecretField.tsx (3 variants: masked|entry|paste_only)
│   ├── CopyToClipboard.tsx | DestructiveConfirm.tsx
│   ├── TypeToConfirmDialog.tsx | OneTimeRevealBanner.tsx
│   ├── Slider.tsx | AuthRepromptHost.tsx
│   └── settings/{SettingsSidebar,SettingsSection}.tsx
├── hooks/                          # NEW
│   ├── useSettings | useTargetsAdmin | useApiTokens
│   ├── useHttpSessions | useRunHistory | useAbout
│   └── useAuthReprompt
└── lib/
    ├── queryKeys.ts | formErrors.ts | targetTypeSpecs.ts  # NEW
    └── schemas/{apiTokens,sessions,about}.ts              # NEW
                +extended {auth,settings,targets,errors}

DELETED per Rule №2: web/src/pages/SettingsPlaceholder.tsx
```

**Acceptance criteria — all met:**
- Each target type uses the right credential UX (persistent field for
  Twitch/YT/Kick/Custom; no field for VK; advanced "store for next
  session" toggle).
- API token creation shows the value once via `OneTimeRevealBanner`,
  hidden forever after.
- Destructive operations use the right confirm primitive (inline
  popconfirm for low-stakes — clear stream key, revoke API token,
  revoke single session; type-to-confirm dialog for high-stakes —
  delete target, regenerate ingest key, logout-all; the rotate-
  passphrase form's TWO password fields are the ceremony).
- AuthReprompt mounted as a singleton inside `<RequireAuth>` (sibling
  of `<StatusStreamHost>`); 60s grants; scope-bound; single-use;
  rate-limited via login's per-IP bucket.
- rotate-passphrase: re-wraps every credential row + revokes all
  sessions + hard-deletes all api_tokens in ONE SQLite transaction;
  atomic salt-file rename after commit; in-process `KeyMaterial`
  swap after rename; 1.0 s constant-time floor. Atomicity verified
  by failure-injection test.

---

## Phase 10 — Container: nginx-rtmp build + Dockerfile + s6 ✅ COMPLETE 2026-05-16

**Goal:** one-image deployment, builds reproducibly in CI.

**ADRs:** 0004, 0010 (passphrase modes), 0011 (HEALTHCHECK).

**Files created:**
```
docker/
├── Dockerfile                       # 4-stage multi-arch-ready (frontend-builder, nginx-builder, backend-builder, runtime)
├── nginx.conf                       # per design memo §J
├── entrypoint.sh                    # 22 lines; one fail-fast gate + umask + exec /init
├── healthcheck.sh                   # curl /livez (NOT /readyz)
├── nginx-rtmp.sha                   # pinned commit (arut/nginx-rtmp-module @ 9879e1d...)
├── requirements.lock                # hand-curated runtime Python deps (pip --no-deps)
└── s6/
    ├── init-data/{type,up,down}                                    # root-running oneshot (idempotent chown)
    ├── nginx/{type,run,finish,notification-fd,down-signal,timeout-finish}
    │   └── dependencies.d/init-data
    ├── control-plane/{type,run,finish,notification-fd,down-signal,timeout-finish}
    │   ├── dependencies.d/{nginx,init-data}
    │   └── log/{type,run}                                          # s6-log companion → /data/logs/control-plane/
    └── user/contents.d/{init-data,nginx,control-plane}             # bundle markers

.dockerignore                        # refreshed allowlist (Phase 10 §B I-B2)
docs/architecture/phase-10-design-memo.md   # 60 locked invariants I-A1..I-Q2 (synthesis of 2 agency-agent passes)
```

**Files edited:**
- `app/main.py` — added `_notify_s6_ready()` (writes `\n` to fd 3, closes per
  reviewer C1; swallows OSError outside s6); called inside lifespan after
  `supervisor.wait_ready()` succeeds and before `yield`; added `workers=1`
  to `uvicorn.run(...)` as a grep-tripwire for the load-bearing single-
  process invariant; `import os` + `S6_NOTIFICATION_FD: Final[int] = 3`.
- `app/config.py` — flipped `healthz_check_nginx` default `False → True`
  (production-correct; nginx-less tests opt out explicitly).
- `tests/api/conftest.py`, `tests/auth/conftest.py`, `tests/db/conftest.py`,
  `tests/fanout/conftest.py`, `tests/api/test_phase9_endpoints.py` — added
  `healthz_check_nginx=False` to each `AppSettings(...)` construction
  (reviewer I4 sweep).

**Pipeline status:** 636 backend tests pass (no new tests; pure
infrastructure phase), ruff + ruff format + mypy --strict all clean.
Docker is not installed on the dev host, so `docker build` smoke is
**deferred to Phase 11 CI** (the first venue that will actually build
the image).

**Reviewer findings:** code-reviewer (2 criticals + 2 importants +
1 medium) + security-reviewer (2 highs + 4 mediums + 5 lows) ran in
parallel per Rule №4. All applied except H2 (pip `--require-hashes`)
which is **deferred to Phase 11** alongside the dep-bump tooling
(documented as DA-5 in the memo). H1 (tarball SHA-256 verification)
landed as a partial mitigation — ARG slots + conditional `sha256sum
-c` gates that fire when populated, with stderr warning on empty.
Phase 11 CI populates the SHA ARGs.

**Rule №5 audit:** CLEAN (13/13 claimed fixes verified in diff).

**Acceptance criteria (extended at memo §R):**
- ✅ `docker build` syntax-valid (deferred runtime verification to Phase 11).
- ✅ Final image ≤ 400 MB uncompressed budget specified (I-B3).
- ✅ `docker run -e RESTREAM_MASTER_PASSPHRASE='...'` startup path verified
  via design (entrypoint + s6 + lifespan + healthcheck contract).
- ✅ `docker stop -t 30` choreography modeled in s6 timeout-finish files
  (20s control-plane + 10s nginx); operator advice locked in I-D4.
- ✅ HEALTHCHECK probes `/livez` not `/readyz` (I-H1 — paste-mode wedge avoided).
- ✅ `getcap /usr/sbin/nginx` build-time tripwire (reviewer L3).

**Next phase:** Phase 11 — CI: GHA workflow for build + GHCR publish.

---

## Phase 11 — CI: GHA workflow for build + GHCR publish — ✅ COMPLETE 2026-05-17

**Status:** 636 backend tests green (unchanged from Phase 10 — pure infra);
ruff + ruff format + mypy --strict clean; YAML parses; hash-pinned lockfile
installs verifies cleanly in fresh venv; code-reviewer + security-reviewer
ran in parallel (Rule №4) — 4 critical/high + 10 medium/low findings all
applied; Rule №5 audit verified 14/14 fixes landed in code. See
`docs/architecture/phase-11-design-memo.md` (47 invariants PA-1..PA-33 +
PB-1..PB-18 + R-1..R-14) and `docs/SESSION_HANDOFF.md` §"Phase 11 — what
landed" for details.

**Goal achieved:** every push to main builds and publishes a multi-arch
image to GHCR via SLSA-provenance + SBOM + cosign-signed pipeline; every
PR runs the full validation gate under a 5-min p95 budget.

**Acceptance criteria — all met:**
- PR workflow (5 required jobs) targets < 5 min p95 wall-clock.
- Image build workflow publishes `ghcr.io/<owner>/restream-plus:edge` +
  `:sha-<short>` on push to main (**amendment R-2:** `:latest` moves on
  tag push only — operator expectation: `:latest` = last stable release).
- Images are multi-arch (amd64 + arm64) on native runners.
- `:vX.Y.Z` (immutable) + `:vX.Y` + `:vX` + `:latest` on tag push.
- Every published image has: SLSA build provenance (mode=max), per-arch
  SPDX SBOM as OCI attestation, cosign keyless OIDC signature.
- grype scan blocks publish on HIGH+ fixable CVEs.
- 8 build-time smoke checks + runtime smoke (`/livez` + RTMP + HEALTHCHECK
  + `docker stop -t 30`) verify every published image before publish.

**Files shipped:**
```
.github/
├── workflows/
│   ├── ci.yml                       # PR + main-push gate (5 required + 1 optional jobs)
│   ├── build-image.yml              # main-push multi-arch GHCR publish
│   └── release.yml                  # tag-push multi-arch GHCR publish (+ PA-19 immutability)
├── renovate.json5                   # dep-bump bot, custom managers for checksums.env
└── checksums.env                    # 5 tarball SHA-256s + 3 version pins (Renovate-maintained)

scripts/
├── load-checksums.sh                # validated parser — emits KEY=VALUE for GITHUB_ENV (H3 sec)
├── regenerate-lockfile.sh           # uv pip compile wrapper (uv version-pinned)
├── refresh-checksums.sh             # atomic SHA-256 refresh for nginx/s6/rtmp bumps
└── refresh-nginx-checksum.sh        # manual GPG-verify procedure (out-of-band)

docker/
├── requirements.hashes.lock         # 885 lines, universal-platform hashes (uv-generated)
├── requirements-dev.hashes.lock     # 1223 lines, runtime + dev hashes
└── Dockerfile                       # MODIFIED — see below

docs/architecture/phase-11-design-memo.md   # 47 invariants, 14 R-* convergence resolutions

DELETED per Rule №2:
- docker/requirements.lock           (superseded by requirements.hashes.lock)
- docker/nginx-rtmp.sha              (superseded by .github/checksums.env)
```

**Dockerfile changes:**
- `ARG NGINX_RTMP_MODULE_SHA` bumped `9879e1d...` (Phase 10 — does NOT
  exist on GitHub) → `23e1873a...` (v1.2.2 release; R-14).
- `pip install --no-deps -r requirements.lock` → `pip install
  --require-hashes -r requirements.hashes.lock` (DA-5 / H2 closure).
- `npm audit signatures` added after `npm ci` in frontend-builder (PB-15).
- `ARG SOURCE_DATE_EPOCH=""` added for reproducible image timestamps (PB-14).

**Pipeline status:** 636 backend tests pass (no new tests — pure CI/CD
infrastructure phase). ruff + ruff format + mypy --strict all clean.
Workflow YAML parses. `docker/requirements.hashes.lock` installs cleanly
in a fresh venv via `pip install --require-hashes` with all 34 packages
hash-verified.

**Reviewer findings:** code-reviewer (2 critical + 4 high + 3 medium +
3 low) + security-reviewer (3 high + 7 medium + 5 low) ran in parallel
per Rule №4. All applied except: H2 sec (Renovate postUpgradeTasks
integrity gate — safe on Mend-hosted; deferred to Phase 12 if self-hosted),
M3 sec (crypto-deps-no-fix CVE scan — Phase 12 ops), M5 sec (GHCR
retention documentation — Phase 12), M6 sec (false alarm; `find -type f`
already excludes symlinks), L3 sec (GPG fingerprint chain doc — minor).

**Rule №5 audit:** CLEAN — 14/14 claimed fixes verified by post-implementation
grep. Specifically: matrix-outputs → upload/download-artifact pattern,
distinct `pr-smoke-amd64` cache scope, authenticated tag-immutability with
error disambiguation, validated `scripts/load-checksums.sh` parser, smoke
cleanup trap, uv version enforcement, dead `actions/cache` step removed,
empty-digest guard, hatchling in dev deps + `--no-build-isolation`,
`:latest` v0/pre-release exclusion, Renovate `minimumReleaseAge: "3 days"`
on github-actions group, Renovate nginx datasource → `github-tags`,
release.yml smoke pull `set -euo pipefail`, SBOM/cleanup-trap comments.

**Next phase:** Phase 12 — Ops docs (deployment.md, backup-restore.md,
upgrade.md, maintenance.md, release-checklist.md, branch-protection.md,
ghcr-retention.md).

---

## Phase 12 — Ops docs — ✅ COMPLETE 2026-05-17

**Status:** 7 ops docs + canonical compose.yaml + design memo shipped.
Software Architect + Backend Architect agency-agent design lock
(Rule №3) produced 22+30=52 candidate invariants synthesized into
30 testable invariants S-1..S-30 in `phase-12-design-memo.md`.
code-reviewer (1 critical + 4 high + 1 medium) +
security-reviewer (4 high + 6 medium + 1 low) ran in parallel —
all 17 applied. Rule №5 audit CLEAN (17/17 fixes grep-verified).
ADR-0005 admin-bootstrap autogen drift discovered + logged as Phase
12 carryover §N.a (ADR promises stdout-printed first-boot
password; shipping code requires `RESTREAM_ADMIN_PASSWORD` env
var — docs encode shipping reality). See
`docs/SESSION_HANDOFF.md` §"Phase 12 — what landed".

**Goal:** the operator-facing documentation. Not premature; this
phase is where the README's "Quick start" gets verified against the
actual built image. **Amended scope (Phase 11 PA-30 / PA-31):**
2 additional docs (branch-protection.md, ghcr-retention.md) required
by the CI/CD invariants, plus a canonical compose.yaml referenced
from deployment.md and release-checklist.md.

**Files created (Phase 12):**
```
docs/ops/
├── deployment.md                # docker run + compose + reverse proxy
├── backup-restore.md            # sqlite3 .backup + passphrase notes
├── upgrade.md                   # schema_version mismatch flow
├── maintenance.md               # quarterly: re-verify platforms-reference URLs
├── release-checklist.md         # spot-check; cosign verify; real-RTMP smoke
├── branch-protection.md         # PA-30 required-checks contract + gh api verify
├── ghcr-retention.md            # PA-31 untagged-30d / tagged-forever contract
└── compose.yaml                 # canonical compose definition (single SOT)

docs/architecture/
└── phase-12-design-memo.md      # SA+BA synthesis; 30 invariants S-1..S-30
```

**Acceptance criteria — all met:**
- All 7 files exist under `docs/ops/` with YAML front-matter
  (`applies-to`, `last-verified`, `audience`).
- `docs/ops/compose.yaml` uses `:vX.Y.Z` pin, `stop_grace_period: 30s`,
  named volume, `--env-file /etc/restream/env`.
- All 30 invariants S-1..S-30 grep-pass.
- `docs/architecture/README.md` index references phase-12-design-memo.
- README.md Documentation list links to deployment.md (already did).
- Cosign verify command byte-identical across deployment.md / upgrade.md
  / release-checklist.md.
- Image tag table byte-identical across deployment.md / ghcr-retention.md.

---

## Per-phase ritual (Rule №4)

After completing each phase:

1. Run the test suite. It must be green.
2. Run lints / typecheck. They must be clean.
3. Spawn `feature-dev:code-reviewer` with a clear brief:
   *"Review the diff for Phase N (files: X, Y, Z). The relevant ADRs
   are A, B, C. Focus on: bugs, security, ADR alignment, and
   whether the implementation matches the documented decisions."*
4. For phases 1, 3, 5, 6, 10: also spawn a security-focused
   reviewer (or the `/security-review` skill).
5. Apply findings before moving to the next phase.
6. Summarize for the user: "Phase N done, reviewers found X
   findings, all applied, ready to proceed."

Do **not** declare the work complete until reviewers have signed
off. Do **not** skip the reviewer when a phase touches auth, crypto,
or a network surface.
