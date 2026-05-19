# Phase 6 — Design memo

Backend Architect lock-in pass before code. Decisions are final unless a
new threat-model fact appears.

## Q1 — App factory shape

`create_app(settings: AppSettings) -> FastAPI` lives in `app/main.py`,
with a single `@asynccontextmanager async def lifespan(app)` attached
via `FastAPI(lifespan=lifespan)`. The factory is the only place that
constructs `KeyMaterial`, the `AsyncEngine`, the `async_sessionmaker`,
the `LoginRateLimiter`, the `RepromptStore`, the `Supervisor`, and the
`EventBus`; nothing is module-global. The lifespan attaches the
already-built `AuthState` (per the dataclass at `app/auth/deps.py:96`)
to `app.state.auth` and additionally `app.state.supervisor`,
`app.state.event_bus`, and `app.state.settings`. Single lifespan, not
nested — Starlette's lifespan protocol is one async-context per ASGI
app, and ADR-0001's "two TaskGroups" is satisfied by nesting
`Supervisor.run()`'s TG inside the lifespan-owned API TG (Q2), not by
nesting two lifespans. uvicorn entrypoint `python -m app.main` instantiates
the app via `create_app(AppSettings())` so factory-style tests can pass
a synthetic settings object without env-var gymnastics.

## Q2 — Two-TaskGroup choreography

The lifespan is the only sane host for the API TG, because Starlette
requires `yield` exactly once and serves requests strictly between the
pre-yield and post-yield halves. Concrete shape (`app/main.py`):

```
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: AppSettings = app.state.settings
    # 1. Boot-time sync work (no TG yet).
    engine = create_async_engine(...)
    sessionmaker_ = async_sessionmaker(engine, expire_on_commit=False)
    await initialize_schema(engine, settings)
    key_material = KeyMaterial(...)
    if settings.passphrase_source == "env":
        await key_material.unlock(settings.master_passphrase.get_secret_value())
    rate_limiter = LoginRateLimiter()
    reprompts = RepromptStore()
    bus = EventBus()
    supervisor = Supervisor(
        sessionmaker=sessionmaker_, key_material=key_material,
        process_spawner=AsyncioProcessSpawner(),
        credential_registry=get_global_credential_registry(),
        ingest_loopback_url=f"rtmp://127.0.0.1:1935/live/{settings.ingest_key_current}",
    )
    app.state.auth = AuthState(key_material, sessionmaker_, rate_limiter,
                               reprompts, settings.trusted_proxies)
    app.state.supervisor = supervisor
    app.state.event_bus = bus
    # 2. Open the API TG; supervisor is its first child.
    async with asyncio.TaskGroup() as api_tg:
        sup_task = api_tg.create_task(supervisor.run(bus), name="supervisor")
        broadcaster_task = api_tg.create_task(  # see Q3
            ws_broadcaster(bus, app.state.ws_subscribers), name="ws-broadcaster")
        reprompt_pruner = api_tg.create_task(
            _reprompt_pruner_loop(reprompts), name="reprompt-pruner")
        try:
            await supervisor.wait_ready(timeout=10.0)
        except BaseException:
            # 3a. Boot crash BEFORE yield: cancel the TG cleanly.
            sup_task.cancel(); broadcaster_task.cancel(); reprompt_pruner.cancel()
            raise
        try:
            yield  # ←-- requests are served here
        finally:
            # 3b. Shutdown: stop supervisor, cancel housekeeping, exit TG.
            with contextlib.suppress(Exception):
                await supervisor.stop(grace=timedelta(seconds=10))
            broadcaster_task.cancel()
            reprompt_pruner.cancel()
    # 4. Post-TG: dispose engine + KeyMaterial.zero() in a final try/finally.
    try:
        await engine.dispose()
    finally:
        key_material.zero()
```

Cleanup ordering is enforced by structured concurrency: the API TG's
`__aexit__` runs after the `finally`, so `supervisor.stop()` (which sets
`_stop_event`) lets the supervisor TG's nested `__aexit__` drain its
heartbeat/drop-alert tasks before the API TG awaits the supervisor task.
On a pre-yield boot crash, an `ExceptionGroup` propagates out of the TG
and the lifespan re-raises — uvicorn aborts startup, exactly what we
want. No `AsyncExitStack` needed; one TG is sufficient because the
supervisor manages its own inner TG (see `supervisor.py:171`).

## Q3 — Per-WS-client fan-out + backpressure

The single drainer is a long-lived task `ws_broadcaster(bus, registry)`
inside the API TG (see Q2), which is the only legal consumer of
`bus.drain()` per `event_bus.py:85`. The registry is a
`set[WsSubscriber]` guarded by an `asyncio.Lock`; each WS handler
appends a fresh `WsSubscriber(queue: asyncio.Queue[BusEvent], ...)` on
connect and removes it on disconnect (try/finally around `accept()`).
Per-client `Queue(maxsize=256)` — not 10 000: the bus already buffers
that for everyone, and a stuck WS client should fail fast rather than
hold a quarter-million-byte tail. On `put_nowait` failure the
broadcaster does **not** drop-oldest (which would silently desynchronise
the client's view); instead it sets a `subscriber.overflowed = True`
flag and the WS handler closes the socket with code 1011 next iteration
("server-side overload — please reconnect for fresh state"). On reconnect
the client receives a `state.full` snapshot (`{run_state, targets:
[{id, ui_state, snapshots, recent_log_lines}], dropped_total}`), built
synchronously from `supervisor.run_state()` + `supervisor.targets()` +
`bus.dropped_total` BEFORE any incremental events are forwarded. This
gives the client a deterministic resync point and matches ADR-0003's
"Supervisor never blocks on slow consumer" — the broadcaster only ever
non-blocking-puts into per-client queues.

## Q4 — WS auth + message schema

Cookie-only on WS, confirmed: bearer-on-WS adds attack surface (no
browser will send `Authorization` over WS upgrade automatically, so the
only callers would be intentional scripts that already have the cookie
flow available, and accepting bearer-on-WS makes the same session
verifier two transports to harden). FastAPI exposes `websocket.cookies`
on the `WebSocket` object before `accept()`; the handler reads
`__Host-rp_session`, calls `session_service.verify_cookie(value)`, and
either `await websocket.accept()` or `await websocket.close(code=4401)`
(custom 4401 = "auth failed", per RFC 6455 reserved-for-application
range; lets the client distinguish from a network 1006). The locked-mode
guard from Q5 is applied identically — locked → `close(code=4503)`. Wire
envelope: every message is `{"v": 1, "event": "<dotted-name>",
"data": {...}}`. Versioning so a Phase 8 frontend pinned to v1 still
works after a future event-shape change. Event names mapped from
`BusEvent.payload` types: `RunStateChangedEvent` → `run.state.changed`
(`{new_state, previous_state, cause}`), `TargetSnapshotEvent` →
`target.snapshot` (`{target_id, ui_state, snapshots_by_role:
[{role, state, last_progress?, retry_attempt, breaker_open}, ...]}`),
`DropAlertEvent` → `bus.drop_alert` (`{dropped_total}`), plus the
synthetic `state.full` (Q3) emitted only on connect. No `WorkerEvent`
crosses the WS boundary — only the projected `TargetSnapshotEvent` does
(per `supervisor.py:711` design). This keeps the WS contract aligned
with what the Dashboard renders (`docs/ui/ux-flows.md` §2).

## Q5 — Locked-mode router-level guard

Use **middleware**, not a router-level Depends. A Depends
`Depends(require_unlocked_keys)` only fires on routes that declare it,
which is exactly the Phase-3 footgun we are closing — middleware runs
unconditionally for every request and can't be forgotten when a new
router is added. The middleware lives at
`app/api/middleware.py::LockedModeMiddleware`, registered AFTER
`TrustedHostMiddleware` and BEFORE auth deps run (so a locked-mode 503
preempts even cookie parsing). Allowlist (path-prefix match):
`/api/unlock`, `/livez`, `/readyz`, `/healthz`, `/internal/rtmp/*` (the
nginx webhook must answer 503 with a body that nginx can read; see Q9 —
in locked mode the loopback webhook returns 503 so OBS publishes are
rejected per ADR-0010), and the unlock UI's static assets when those
land (Phase 7 mounts `/` for the SPA, so we allowlist `/` and `/assets/*`
GETs in locked mode and rely on the SPA's own routing to show the unlock
screen). Blocked requests get `503 {"detail": "service_locked"}` for
`LOCKED` and `503 {"detail": "service_unlocking", headers:
{"Retry-After": "2"}}` for `UNLOCKING` — same shape `require_unlocked_keys`
already produces (`deps.py:147`), so the frontend has one error code to
handle. Unlock endpoint is rate-limited but with a SEPARATE limiter
(3/IP/15min per ADR-0010 §"Mode 2 — paste"), not the login limiter; it
shares the trusted-proxy IP extractor but has its own bucket dict so
unlock attempts don't lock out future logins and vice versa. CORS is
not configured for v1 (same-origin SPA), so middleware order is
TrustedHost → LockedMode → routers; no third-party middleware in
between.

## Q6 — `api_tokens.user_id` FK schema change

Per Rule №2, edit `schema.sql` in place. Concrete edit at
`app/db/schema.sql:43`:

```sql
CREATE TABLE api_tokens (
    id              TEXT    PRIMARY KEY,
    user_id         TEXT    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      BLOB    NOT NULL UNIQUE,
    label           TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    last_used_at    INTEGER,
    revoked_at      INTEGER
) STRICT;
CREATE INDEX idx_api_tokens_user_id    ON api_tokens(user_id);
CREATE INDEX idx_api_tokens_revoked_at ON api_tokens(revoked_at);
```

Note `users.id` is `TEXT PRIMARY KEY` (`schema.sql:22`), so the FK
column is also `TEXT`. Index, not UNIQUE — one user can hold many
tokens; the index is for "revoke all for this user" (logout-all
analogue) and for the future audit page filter. `ApiTokensRepository.create`
takes `user_id: str` and writes it through; `ApiTokenService.create`
takes `user_id: str` and threads it through; the new
`get_current_user_via_bearer` does `users.get_by_id(token_dto.user_id)`
instead of the hardcoded `"admin"` lookup at `deps.py:319`. The seed
admin row already exists with the bootstrap-generated TEXT id (Phase 2
schema_init); no change needed there. `ApiTokenORM` in
`app/db/models.py` gets `user_id: Mapped[str]` with a
`ForeignKey("users.id")`. Tests to update: `tests/auth/test_api_tokens.py`
(every `create()` call gets `user_id="<test-admin-id>"`),
`tests/auth/test_deps.py` (bearer test creates a non-admin user and
asserts the bearer resolves to THAT user, proving the hardcoded-admin
crutch is gone), `tests/db/test_repositories.py` for the api_tokens
repo, and `test_orm_models_match_schema_sql` picks up the new column
automatically because it AST-walks both sources. `ApiTokenDTO` itself
does not need to expose `user_id` to the UI listing (single-user app),
but it MUST be loaded internally for the `get_current_user_via_bearer`
path — add `user_id: str` to the DTO; this is metadata, not a secret.

## Q7 — Rehash via BackgroundTasks

Login handler signature: `async def login(creds: LoginRequest, background:
BackgroundTasks, response: Response, ...)`. On `LoginOutcome.needs_rehash
== True`, call `background.add_task(_rehash_admin_password,
sessionmaker=auth.sessionmaker, user_id=outcome.user.id,
plaintext=creds.password)`. FastAPI runs background tasks AFTER the
response body is sent (Starlette guarantees this via the response's
`background` attribute), which is exactly what we want — the
~100 ms Argon2id cost is paid off the request hot path and the
timing-channel from Phase 3 stays closed. The task closure must hold
sessionmaker (not a session — sessions are per-request and already
closed by the time the background runs); it opens its own short
session, computes the new hash via `asyncio.to_thread(hash_password,
plaintext)`, calls `UsersRepository.update_password_hash(user_id,
new_hash)`, commits, and `await audit.append("password_rehashed",
user_id=...)` via `AuditLogRepository(sessionmaker)`. Concurrency: two
near-simultaneous logins both rehashing is harmless — each writes its
own valid hash; the last commit wins; both produce a hash that verifies
the same plaintext. Failure handling: wrap the body in
`try/except Exception: _logger.exception("rehash_failed", user_id=...)`
and swallow — the next successful login that still sees `needs_rehash`
will retry. The plaintext lives in Python's request-scoped GC for the
lifetime of the BackgroundTask coroutine; we accept this exposure
because (a) the request just authenticated with this exact plaintext
seconds ago, (b) ADR-0006's threat model already accepts in-process
plaintext.

**Slice 8 amendment (Hex Audit BA-F4, 2026-05-19 — SA2-F9 ADR
closure).** The two-session split described above (one txn for the
hash update, a separately-committed audit row via
`AuditLogRepository(sessionmaker).append(...)`) was the original
forensic gap that slice 8 set out to close. The background task now
opens one session, calls
`UsersRepository.update_password_hash(user_id, new_hash)` AND
`audit.append(session, event_type="password_rehashed",
actor="system")` in the SAME `session.begin()` block, then commits
once. The hash update and the audit row commit atomically (or roll
back together). The `try/except Exception: _logger.exception(...) ;
swallow` posture around the body is preserved — the
fallback-on-next-login semantic still applies — but the audit row is
no longer subject to a separate commit that could silently fail
after the hash already landed. See **ADR-0007 §"Audit-log
durability and transactional shape"** for the full
canonical-vs-standalone repo contract.

## Q8 — Reprompt burn-on-failure DoS surface

Decisions, in order:

- **No grant ID in any URL query string.** Already locked in Phase 3
  (`X-Reprompt-Grant` header per `deps.py:374`); no Phase 6 endpoint
  changes this.
- **No grant ID in any WS message.** All reprompt-protected actions are
  REST mutations; the WS is read-only fan-out.
- **The endpoint that issues the grant does NOT log a warning per call**
  — the grant issuance is a normal-path event and the existing log line
  at `reprompts.py:131` (with the SHA-256 fingerprint) is sufficient for
  audit. Document the burn-on-failure semantic in `docs/ui/ux-flows.md`
  §6 next to the AuthReprompt component spec, and in the Phase 6 CODE_PLAN
  acceptance criteria — the *user-facing* mitigation is "the modal that
  consumes the grant should fire its consume call within seconds of the
  prompt; the user gets one shot per re-prompt, by design." That's
  honest, not crutch.
- **No active mitigation in Phase 6.** IP-binding the grant has been
  weighed and rejected: NAT'd LANs and home reverse proxies (a real
  user environment per ADR-0001) frequently shift the apparent client IP
  between the grant-issue request and the destructive request, which
  would lock the legitimate user out of their own action. The threat
  model already names "an attacker who somehow learns a grant ID" as
  pre-conditioned on a separate compromise the grant ID alone cannot
  cause; pop-before-validate is the correct primitive.

## Q9 — Internal nginx-rtmp webhook auth

Loopback verified by `request.client.host == "127.0.0.1"` (or `::1`),
checked at the top of `internal_rtmp.py::publish` and
`publish_done`; non-loopback gets `403 forbidden` with no body. The
trusted-proxy logic from `auth/rate_limit.py` does NOT apply here —
this endpoint must reject ANY request whose immediate peer is not
loopback, regardless of `X-Forwarded-For`. Routes mounted under the
`/internal` prefix (`app.include_router(internal_router, prefix="/internal")`)
so a future reverse-proxy misconfiguration that routes external
`/internal/*` to FastAPI still 403s on the loopback check. The
locked-mode allowlist (Q5) covers `/internal/rtmp/*` so nginx
webhooks reach the handler in locked mode; the handler then returns
`503 service_locked` (translates to nginx denying the publish, per
ADR-0010 §"Mode 2"). Wire shape: nginx-rtmp posts
application/x-www-form-urlencoded with `name=<ingest-key>` (and
incidental fields like `addr`, `clientid`, `app`, `flashver`); we
read `name` and compare against `settings.ingest_key_current` and
`settings.ingest_key_previous` (timing-safe compare via
`hmac.compare_digest`); on match `200`, on mismatch `403`. On
`publish_done` we 200 unconditionally after notifying the supervisor —
nginx doesn't act on the response body but expects 2xx. The route
ALSO calls `supervisor.notify_obs_publish_began()` /
`notify_obs_publish_ended()`; these are sync methods on the supervisor
(`supervisor.py:365`), no further auth needed beyond the loopback
check (single-admin loopback model).

## Q10 — Error response shape

Keep `{"detail": "code_string"}` for app errors, the FastAPI default
`{"detail": [{...validation errors...}]}` for 422. Wrapping in
`{"error": {...}}` adds noise without solving anything: the frontend
already needs to switch on the structure (string vs list) to render,
and a unified envelope would force ALL handlers — including the dozens
of pure CRUD ones — through a custom exception handler. The `detail`
string is the stable contract. Defined codes (this is the Phase 6
shipping set; new codes added by future phases append, never rename):
`service_locked`, `service_unlocking`, `unauthorized`,
`invalid_credentials`, `rate_limited`, `reprompt_required`,
`reprompt_invalid_or_expired`, `target_not_found`, `target_in_use`,
`target_misconfigured`, `illegal_run_state`, `credential_required`,
`ingest_key_invalid`, `unlock_failed`, `unlock_rate_limited`,
`auth_state_not_initialised`. Per-handler codes that don't fit a generic
verb (e.g., `vk_credential_required` on START) live alongside. Codes
ARE stable contracts — the frontend pins them in `web/src/messages.ts`
(per CODE_PLAN Phase 7) and a rename is a breaking change. Frontend
displays the localized string; the wire code is stable.

## Q11 — Run start/stop endpoint semantics

**Async, both endpoints. 202 Accepted with empty body, client watches
WS for `run.state.changed`.** Sync feels simpler but is wrong:
`supervisor.start_run()` opens a DB session, decrypts every credential
(KEK is fast but the DB read scales with target count), spawns N
ffmpeg processes (each a sub-second `create_subprocess_exec`), and
only returns once `RunState.ARMED`. On a slow disk + 6 targets that's
easily 2–5 s, well past typical reverse-proxy idle thresholds (Cloudflare
100s, but nginx default 60s, and a homelab Caddy with `request_body_timeout
30s`). More importantly, the WS is the source of truth for state during
a run anyway — making the REST call sync would force the frontend to
maintain TWO state sources ("did my POST resolve?" + "did the WS event
arrive?") and reconcile them. Spawn the supervisor call as a fire-and-
forget task held in `app.state` so the caller can't await it but the
lifespan can cancel it on shutdown:
`asyncio.create_task(supervisor.start_run())`, with the task added to
a strong-ref set on `app.state.supervisor_tasks` (mirrors the pattern
at `supervisor.py:144`). On illegal-state-transition, the supervisor
raises `IllegalRunStateTransitionError` synchronously BEFORE the task
is created — we let the early raise turn into `409 illegal_run_state`,
then-and-only-then return 202 if the transition is legal. Same shape
for `/api/run/stop`.

## Q12 — `/readyz` semantics

Concrete checks, in order, each with a 500 ms total budget:

1. **DB**: `SELECT 1` on a fresh session with `asyncio.timeout(0.1)`;
   `{"ok": bool, "latency_ms": float}`. ADR-0011's write-canary against
   a `health_probe` row is over-engineered for v1 — SQLite WAL writes
   are sub-ms and a separate write probe doubles DB ops per readiness
   poll without surfacing a failure mode the read can't already detect.
   Stick with `SELECT 1` for Phase 6; revisit if a real wedged-WAL
   incident appears.
2. **Supervisor heartbeat**: `not supervisor.heartbeat_is_stale()`
   (which compares against `HEARTBEAT_STALE_AFTER = 10s` per
   `supervisor.py:77`); `{"ok": bool, "last_tick_ms_ago": int}`.
3. **Event bus**: `bus.dropped_total` is reported as
   `{"ok": True, "dropped_total": int}` — non-zero is a **warning in
   the body, not a 503**. Drops are a slow-consumer signal we want
   surfaced, not a pager — the supervisor explicitly survives them
   (ADR-0003 §"Concurrency"), so failing readiness on drops would
   trigger reverse-proxy failovers that don't fix the underlying issue.
4. **Passphrase**: `key_material.state == READY`;
   `{"ok": bool, "state": "locked|unlocking|ready"}`.
5. **nginx-rtmp**: configurable. `settings.healthz_check_nginx: bool`
   (default `False` for Phase 6 dev where there's no nginx; default
   flips to `True` in Phase 10 when the s6 bundle ships). When enabled,
   TCP-connect to `127.0.0.1:1935` with a 50 ms timeout, cached for 2 s
   per ADR-0011.

`/readyz` returns `200 {"status":"ready", "checks":{...}}` if every
`ok=True`, else `503 {"status":"not_ready", "checks":{...}}` (same
schema both ways so the frontend can render). `/livez` is unconditional
`200 {"status":"alive"}`. `/healthz` is the `/readyz` handler aliased
via `router.get(["/readyz", "/healthz"])`.

## Q13 — Tests

`httpx.AsyncClient(transport=ASGITransport(app=app))` against the real
FastAPI app instance; no real network sockets, no uvicorn, no port
binding. WebSocket tests use Starlette's `TestClient(app).websocket_connect(...)`
which speaks the in-memory ASGI WS protocol — `httpx` doesn't yet do WS
cleanly, and `websockets` requires a real port. Faking the supervisor:
introduce `app/fanout/_testing.py::FakeSupervisor` (NOT in `tests/` —
it's part of the supervisor's public test surface, like Phase 5's
`FakeProcessSpawner` at `tests/fanout/_fakes.py`) implementing the
exact public surface the handlers call (`start_run`, `stop_run`,
`enable_target`, `disable_target`, `reset_target_worker`,
`notify_obs_publish_began`, `notify_obs_publish_ended`, `run_state`,
`targets`, `heartbeat_age`, `heartbeat_is_stale`, `recent_log_lines`)
plus deterministic state transitions and an inject-event method
(`inject_target_snapshot(target_id, ui_state)`) that pushes onto the
real `EventBus`. `tests/api/conftest.py` builds a real lifespan-wired
app via `create_app(test_settings)` BUT swaps `Supervisor` for
`FakeSupervisor` via dependency injection on the `create_app` factory
(add a kwarg `supervisor_factory=Supervisor` so tests can pass
`FakeSupervisor`). Tests get an `httpx.AsyncClient` per test (not per
session) so cookie state isolates. Real ffmpeg is never spawned. The
Phase 5 `FakeProcessSpawner` is NOT used here because handlers don't
talk to processes directly — they talk to the supervisor — but a few
end-to-end tests (start → armed → simulated OBS publish webhook → live
→ stop) use the REAL `Supervisor` with `FakeProcessSpawner` to assert
the handler-to-supervisor wiring is correct. Two layers of integration:
fast handler tests (FakeSupervisor) for breadth, slow end-to-end tests
(real Supervisor + FakeProcessSpawner) for the wiring contract.

## Q14 — settings_api / sessions_history / api_tokens_api endpoints

All require `AuthenticatedRequestDep` (cookie or bearer); all 503 in
locked mode via the middleware (Q5).

- `GET /api/settings` — `SettingsDTO` (idle_timeout, log_retention,
  ingest_key_current MASKED to last4, ingest_key_rotated_at).
- `PATCH /api/settings` — partial update for `idle_timeout_seconds`
  and `log_retention_days`. No reprompt; benign settings.
- `POST /api/settings/rotate-ingest-key` — reprompt
  `RepromptScope.REGENERATE_INGEST_KEY`; uses
  `SettingsRepository.rotate_ingest_key`; returns the new key plaintext
  ONCE (banner UX in Phase 9). The previous key remains valid for the
  configured grace window.
- `GET /api/sessions?limit=&before=` — paginated run history (cursor
  on `started_at`, descending). Read-only.
- `GET /api/security/tokens` — list `ApiTokenDTO` metadata (no
  plaintext, no token_hash).
- `POST /api/security/tokens` — body `{label}`; returns
  `{plaintext, dto}`. The plaintext appears here exactly once.
- `DELETE /api/security/tokens/{id}` — reprompt
  `RepromptScope.REVOKE_API_TOKEN`; soft-revoke via `revoked_at`.
- `POST /api/auth/change-password` — reprompt
  `RepromptScope.CHANGE_PASSWORD`; revokes all sessions for the user
  except the current one, re-issues the current cookie.
- `POST /api/auth/logout` — deletes the current session row.
- `POST /api/auth/logout-all` — deletes all session rows for the
  current user. No reprompt needed (the user is already authenticated
  on this device; revoking other devices is not destructive of stream
  state).

## Q15 — Module surface inside `app/api/`

Add three modules to the CODE_PLAN list. Final shape:

- `__init__.py` — public re-exports.
- `auth.py` — login, logout, logout-all, change-password, unlock.
- `targets.py` — CRUD targets + per-target credential write.
- `run.py` — `/api/run/start`, `/api/run/stop`.
- `settings_api.py` — settings GET/PATCH + rotate-ingest-key.
- `api_tokens_api.py` — list/create/revoke API tokens.
- `sessions_history.py` — paginated run history.
- `internal_rtmp.py` — nginx webhooks.
- `ws.py` — WS handler + per-client subscriber wiring.
- `health.py` — `/livez` `/readyz` `/healthz`.
- **`schemas.py`** (NEW) — every Pydantic request/response model.
  Centralised so OpenAPI sees one source per shape and the frontend's
  generated types (Phase 8) don't fragment. Endpoints stay thin —
  validation lives in the schema, business in services.
- **`errors.py`** (NEW) — exception → HTTPException translation, the
  Q10 error-code constants as a `Final` enum, and one
  `app.add_exception_handler` for `IllegalRunStateTransitionError` →
  `409 illegal_run_state` (the rest are raised as `HTTPException`
  directly per Phase 3 convention).
- **`middleware.py`** (NEW) — `LockedModeMiddleware` from Q5.
- `app/main.py` — factory + lifespan (Q1, Q2).

## Module file map

Reconciled with `docs/CODE_PLAN.md` Phase 6:

```
app/
├── api/
│   ├── __init__.py
│   ├── auth.py
│   ├── targets.py
│   ├── run.py
│   ├── settings_api.py
│   ├── api_tokens_api.py
│   ├── sessions_history.py
│   ├── internal_rtmp.py
│   ├── ws.py
│   ├── health.py
│   ├── schemas.py        # NEW vs CODE_PLAN
│   ├── errors.py         # NEW vs CODE_PLAN
│   └── middleware.py     # NEW vs CODE_PLAN
├── fanout/
│   └── _testing.py       # NEW: FakeSupervisor (public test surface, Q13)
└── main.py               # create_app + lifespan
```

CODE_PLAN.md should be amended to add the three new modules to the
Phase 6 file manifest before coding starts; this is a documentation
edit, not a scope change.

## Cleanup of deferred Phase 3 gaps

Each of the four Phase 3 deferred gaps maps to exactly one question in
this memo:

- **Router-level locked-mode guard** → **Q5**. Resolved by middleware
  at `app/api/middleware.py::LockedModeMiddleware` registered between
  TrustedHost and the routers; allowlist covers the five paths that
  must answer in locked mode.
- **Hardcoded `"admin"` in `get_current_user_via_bearer`** → **Q6**.
  Resolved by the `api_tokens.user_id` FK schema edit + repo signature
  change + `deps.py` change to load the user via `token_dto.user_id`.
- **Reprompt burn-on-failure DoS** → **Q8**. No active mitigation
  added in Phase 6 (defended); documented in `docs/ui/ux-flows.md` §6
  next to the AuthReprompt component, and the existing
  SHA-256-fingerprint log line at `reprompts.py:131` is sufficient
  audit signal.
- **First-login rehash via BackgroundTasks** → **Q7**. Resolved by
  injecting `BackgroundTasks` on the login handler and scheduling
  `_rehash_admin_password(sessionmaker, user_id, plaintext)` when
  `LoginOutcome.needs_rehash` is `True`; failure-tolerant; idempotent
  under races.
