"""FastAPI app factory + lifespan + uvicorn entrypoint.

Per Phase 6 design memo §Q1, §Q2: single `create_app(settings) ->
FastAPI` factory, single lifespan, two-TaskGroup architecture.

The factory creates `KeyMaterial` (in LOCKED state) and the
`LockedModeMiddleware` BEFORE returning the app, because Starlette
builds its middleware stack on the first request — adding middleware
inside the lifespan never takes effect.

The lifespan then:

  1. Sync boot: engine, sessionmaker, schema init.
  2. If env mode: unlock the already-created KeyMaterial (paste mode
     stays LOCKED until /api/unlock).
  3. Build LoginRateLimiters, RepromptStore, EventBus, Supervisor.
  4. Attach AuthState, Supervisor, EventBus, WsRegistry to `app.state`.
  5. Open the API TaskGroup. First child: `supervisor.run(bus)`. Also
     in the TG: `ws_broadcaster`, `reprompt_pruner`.
  6. `await supervisor.wait_ready(timeout=10s)`.
  7. `yield` — requests are served.
  8. On shutdown: `supervisor.stop()`, cancel broadcaster / pruner,
     exit the TG, dispose engine.

Boot-crash and shutdown choreography uses structured concurrency: a
TaskGroup `__aexit__` propagates / waits naturally, and the
supervisor's own inner TG (see `app/fanout/supervisor.py::run`) nests
inside this outer TG so a supervisor crash propagates here.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from app.api import LockedModeMiddleware, install_exception_handlers
from app.api.api_tokens_api import router as api_tokens_router
from app.api.auth import auth_router, unlock_router
from app.api.health import router as health_router
from app.api.internal_mtx import router as internal_mtx_router
from app.api.internal_rtmp import router as internal_rtmp_router
from app.api.run import router as run_router
from app.api.security_api import about_router, security_router
from app.api.sessions_history import router as sessions_history_router
from app.api.settings_api import router as settings_router
from app.api.targets import router as targets_router
from app.api.ws import WsRegistry, ws_broadcaster
from app.api.ws import router as ws_router
from app.auth.deps import AuthState
from app.auth.key_material import KeyMaterial, load_or_create_kdf_salt
from app.auth.last_seen_coalescer import LastSeenCoalescer
from app.auth.rate_limit import LoginRateLimiter
from app.auth.reprompts import RepromptStore
from app.config import AppSettings
from app.db import create_engine, initialize_schema, make_sessionmaker
from app.fanout.event_bus import EventBus
from app.fanout.host_stats import (
    HostStatsSampler,
    NginxRtmpStatFetcher,
    ProcfsStatReader,
    WorkerPidProvider,
)
from app.fanout.process import AsyncioProcessSpawner, ProcessSpawner
from app.fanout.supervisor import Supervisor
from app.logging_setup import configure_logging, get_credential_registry
from app.repositories.audit_log import AuditLogRepository
from app.version import BUILD_SHA, __version__

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.fanout.supervisor import WorkerFactory

    SupervisorFactory = Callable[..., Supervisor]
    # Hex Audit CR2-Re-F4 (slice 10): named alias for the sessionmaker
    # type so background-loop signatures can drop their `: object` +
    # `# type: ignore[arg-type|operator]` workarounds without forcing
    # a runtime import that would create a cycle. The TYPE_CHECKING
    # guard means there is NO runtime cost — mypy sees the precise
    # type; the interpreter never imports `sqlalchemy.ext.asyncio` at
    # this module's load time.
    _AsyncSessionmaker = async_sessionmaker[AsyncSession]

_logger = structlog.get_logger(__name__)

LIFESPAN_SUPERVISOR_READY_TIMEOUT: Final[float] = 10.0
"""Seconds the lifespan waits for `supervisor.wait_ready()` before
declaring boot failed and aborting startup. The supervisor TG sets
the ready event immediately on entry, so 10s is generous — a
deployment that exceeds this is wedged for reasons unrelated to
supervisor startup itself."""

LIFESPAN_SHUTDOWN_GRACE: Final[timedelta] = timedelta(seconds=15)
"""Per-worker SIGTERM→SIGKILL grace on shutdown via the lifespan.
Bumped from supervisor's WORKER_GRACE so the operator's `docker
stop` (default 10s) is closer to a clean drain than a forced kill."""

REPROMPT_PRUNE_INTERVAL_SECONDS: Final[float] = 30.0
"""How often the lifespan ticks `RepromptStore.prune_expired()`.
30s is enough headroom for 60s grants — even when many are issued
and abandoned, the dict can't accumulate more than ~2 windows worth."""

KDF_SALT_PREVIOUS_GRACE: Final[timedelta] = timedelta(hours=24)
"""Phase 9 (ADR-0006 §"Migration / passphrase rotation"): the
`.kdf_salt.previous` file lingers 24 h after a rotation so an operator
can restore from a backup taken before the rotation. The lifespan
runs a periodic GC tick that unlinks the file after its mtime ages
past this window. Loose-tolerance loop (checks every minute) keeps
the operator-facing window approximate, not millisecond-precise."""

KDF_SALT_PREVIOUS_CHECK_INTERVAL_SECONDS: Final[float] = 60.0

INGEST_KEY_PREVIOUS_CHECK_INTERVAL_SECONDS: Final[float] = 60.0
"""Hex Audit BA-F1 (2026-05-18): cadence for the lifespan-side cleanup
loop that clears `settings.ingest_key_previous` + `ingest_key_grace_until`
once the grace window has expired. 60 s matches the kdf-salt cleanup
loop and is well below any realistic operator-facing grace window
(default 24 h), so the wall-clock slop is invisible."""

AUDIT_LOG_RETENTION_CHECK_INTERVAL_SECONDS: Final[float] = 6 * 60 * 60.0
"""Hex Audit FG2-M6 (slice 8): cadence for the audit-log retention
loop. Six hours — coarse enough that the per-tick `DELETE` cost is
negligible even on busy systems, fine enough that the retention floor
is enforced 4 times a day. The loop also drives the periodic
`PRAGMA wal_checkpoint(TRUNCATE)` so audit-write WAL growth doesn't
accumulate without bound."""

AUDIT_LOG_RETENTION_FAILURE_THRESHOLD: Final[int] = 5
"""Slice 8.5 (Re-audit Checkpoint #2, hex-audit findings BA2-F3 +
FG3-F2): after this many consecutive retention-tick failures, the
loop escalates from `_logger.warning` to `_logger.critical`. At a
6 h cadence, 5 failures = ~30 h continuous DB unavailability — a
real ops signal, not a transient hiccup."""

AUDIT_LOG_RETENTION_EXCLUDED_EVENT_TYPES: Final[tuple[str, ...]] = ("audit_log_retention_run",)
"""Slice 8.5 (Hex Audit FG3-F4): event types that the retention loop
NEVER deletes. The `audit_log_retention_run` marker records that
retention ran — including it in the delete predicate creates a
recursive noise pathology (retention deletes its own markers, which
triggers a new marker on the next tick, which itself eventually ages
out, ad infinitum). Markers persist as eternal forensic record of
retention cadence."""

S6_NOTIFICATION_FD: Final[int] = 3
"""Phase 10 §C+§M: file descriptor that s6-overlay's `notification-fd`
mechanism listens on for the "service ready" signal. The s6 service
file `/etc/s6-overlay/s6-rc.d/control-plane/notification-fd` contains
the literal `3\\n`; s6 marks the service `ready` once we write to
fd 3 and close it. In non-s6 environments (dev runs, tests, ad-hoc
`python -m app.main`) fd 3 isn't open — the write raises OSError and
is swallowed."""


def create_app(
    settings: AppSettings | None = None,
    *,
    process_spawner: ProcessSpawner | None = None,
    supervisor_factory: SupervisorFactory | None = None,
    worker_factory: WorkerFactory | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `settings` defaults to a fresh `AppSettings()` which reads env;
    tests pass an explicit one rooted at a tmp_path.

    `process_spawner` and `supervisor_factory` are injection points
    for the test suite — production passes neither and the lifespan
    wires `AsyncioProcessSpawner` + the real `Supervisor`. Tests pass
    a `FakeProcessSpawner` (Phase 5) and / or a `FakeSupervisor`
    factory (Phase 6 design memo §Q13).

    KeyMaterial is constructed HERE (not inside the lifespan) because
    `LockedModeMiddleware` needs a stable reference at middleware-
    registration time. Starlette builds the middleware stack on first
    request, BEFORE the lifespan completes — adding middleware inside
    the lifespan never takes effect for incoming requests. The
    lifespan unlocks the same instance under env-mode and leaves it
    LOCKED under paste-mode; the middleware reads `state` per request,
    so a later /api/unlock takes effect immediately.
    """
    if settings is None:
        settings = AppSettings()

    configure_logging(settings)

    # Build KeyMaterial up front — see docstring for why.
    kdf_salt = load_or_create_kdf_salt(settings.kdf_salt_path)
    key_material = KeyMaterial(kdf_salt=kdf_salt)

    app = FastAPI(
        title="Restream_Plus",
        version=__version__,
        lifespan=_make_lifespan(
            settings=settings,
            key_material=key_material,
            process_spawner=process_spawner,
            supervisor_factory=supervisor_factory,
            worker_factory=worker_factory,
        ),
    )
    # `app.state.settings` is read by `get_settings` and the lifespan;
    # we set it up-front so test apps can inspect it even before the
    # lifespan has executed.
    app.state.settings = settings

    install_exception_handlers(app)
    # Locked-mode middleware MUST be registered before the first
    # request; the same `key_material` instance is shared with the
    # lifespan-built AuthState.
    app.add_middleware(LockedModeMiddleware, key_material=key_material)

    # Mount routers.
    app.include_router(auth_router)
    app.include_router(unlock_router)
    app.include_router(targets_router)
    app.include_router(run_router)
    app.include_router(settings_router)
    app.include_router(api_tokens_router)
    app.include_router(sessions_history_router)
    app.include_router(internal_rtmp_router)
    app.include_router(internal_mtx_router)
    app.include_router(security_router)
    app.include_router(about_router)
    app.include_router(ws_router)
    app.include_router(health_router)

    # Serve the bundled React SPA from /app/web/dist/ (built into the
    # container image by docker/Dockerfile's frontend-builder stage).
    # When the directory is missing — typical for `pytest` runs out of
    # a source checkout where the frontend hasn't been built — we skip
    # the mount entirely so the API surface stays intact for tests.
    spa_dir = Path("/app/web/dist")
    if not spa_dir.is_dir():
        # Fallback: in-tree dev path so `npm run build && python -m app.main`
        # works without copying files into /app.
        candidate = Path(__file__).resolve().parent.parent / "web" / "dist"
        if candidate.is_dir():
            spa_dir = candidate
    if spa_dir.is_dir():
        _mount_spa(app, spa_dir)
    return app


def _mount_spa(app: FastAPI, spa_dir: Path) -> None:
    """Serve the React SPA + history-API fallback.

    A single catch-all GET handler reachable only after all API routers
    have had their chance:
      - `/` and any path that matches an on-disk file under `spa_dir`
        (index.html, /assets/*, /favicon.ico) → serve that file.
      - Any other path that doesn't collide with the API surface
        (anything NOT starting with `api/`, `internal/`, `ws`,
        `livez`, `readyz`) → serve index.html so React Router can
        resolve the deep link client-side.
      - Paths in the API surface that didn't match a router above
        → real 404. We do NOT silently serve the SPA shell on those
        because that would hide frontend fetch typos behind an HTML
        response.

    The mount lives AFTER all `app.include_router(...)` calls so the
    catch-all never shadows a real API endpoint.
    """
    index_html = spa_dir / "index.html"
    api_prefixes: tuple[str, ...] = (
        "api/",
        "internal/",
        "ws",
        "livez",
        "readyz",
    )
    # Cap total request-path length BEFORE calling stat(). Linux ext4
    # ENAMETOOLONG kicks in at 4096 BYTES (not codepoints); we cap well
    # under that so resolved paths can never trip the syscall limit.
    # Hex Audit footgun-hunter FG2-C3 (2026-05-18): the original cap
    # used `len(full_path)` (codepoint count) which is blind to UTF-8
    # multibyte expansion — a 1024-codepoint path of 4-byte codepoints
    # is 4096 bytes after fsencode. The byte-space cap is the
    # authoritative one. Anything longer is either an attack, a
    # router-loop bug, or a typo — falling through to index.html lets
    # the SPA render its own NotFound page rather than leaking a
    # server-side crash to the client.
    max_path_codepoints = 1024
    max_path_bytes = 2048
    resolved_root = spa_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(request: Request, full_path: str) -> FileResponse:
        if any(full_path.startswith(p) for p in api_prefixes):
            raise HTTPException(status_code=404)
        if len(full_path) > max_path_codepoints:
            return FileResponse(index_html)
        # FG2-C3 defence-in-depth: the byte-space check above only
        # falls through if the path looks short in codepoint space; a
        # 600-codepoint string of 4-byte UTF-8 chars (e.g., emoji or
        # rare CJK supplementary plane) is 2400 bytes — past the safe
        # margin even though the codepoint check passes.
        if len(full_path.encode("utf-8", errors="replace")) > max_path_bytes:
            return FileResponse(index_html)
        # `resolve()` collapses `..` segments to defend against path
        # traversal attempting to read files outside spa_dir. A path
        # we can't even resolve (embedded NUL → ValueError, syscall
        # error → OSError) is not a legitimate SPA deep link; we
        # return 404 rather than the SPA shell so the request stays
        # distinguishable from a normal deep-link in access logs.
        try:
            candidate = (spa_dir / full_path).resolve()
        except (OSError, ValueError):
            raise HTTPException(status_code=404) from None
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            raise HTTPException(status_code=404) from None
        # `is_file()` calls stat(), which raises OSError on weird inputs
        # (broken symlinks, ENAMETOOLONG paths the cap above didn't
        # catch, etc.) or ValueError on NUL bytes. Treat all such cases
        # as "not a real file" and fall through to the SPA shell — the
        # handler must never crash regardless of input.
        try:
            is_file = candidate.is_file()
        except (OSError, ValueError):
            return FileResponse(index_html)
        if is_file:
            return FileResponse(candidate)
        return FileResponse(index_html)


def _make_lifespan(
    *,
    settings: AppSettings,
    key_material: KeyMaterial,
    process_spawner: ProcessSpawner | None,
    supervisor_factory: SupervisorFactory | None,
    worker_factory: WorkerFactory | None,
) -> Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        sessionmaker_ = make_sessionmaker(engine)
        await initialize_schema(engine, settings)

        # ADR-0010: env mode unlocks at boot; paste mode stays LOCKED
        # until the operator hits /api/unlock.
        if settings.passphrase_source == "env" and settings.master_passphrase is not None:  # noqa: S105 — Literal value, not a secret
            await key_material.unlock(settings.master_passphrase.get_secret_value())

        rate_limiter = LoginRateLimiter()
        # Unlock limiter is distinct from the login one (ADR-0010 §"Mode 2"
        # — 3/IP/15min). Same class, tighter parameters.
        unlock_rate_limiter = LoginRateLimiter(
            failure_limit=3,
            window_seconds=15 * 60.0,
        )
        # Slice 7 / BA-F3: dedicated limiter for /api/auth/reprompt.
        # Same class as login + unlock, tighter (3/IP/15min) — the user
        # is already authenticated, so the threat surface is a stolen
        # cookie brute-forcing the password from inside the session;
        # smaller blast radius beats login's 5 since legitimate humans
        # only trip this on a typed-wrong-password during a destructive
        # op (where 3 retries is plenty).
        reprompt_rate_limiter = LoginRateLimiter(
            failure_limit=3,
            window_seconds=15 * 60.0,
        )
        reprompts = RepromptStore()
        # Slice 7 / BA-F6+FG2-H1+FG2-M7: shared write-coalescer for
        # `sessions.last_seen_at`. HTTP cookie verification and WS
        # upgrade both consult it; both paths suppress repeated touches
        # within a 60 s window per `token_hash`.
        last_seen_coalescer = LastSeenCoalescer()

        auth_state = AuthState(
            key_material=key_material,
            sessionmaker=sessionmaker_,
            rate_limiter=rate_limiter,
            unlock_rate_limiter=unlock_rate_limiter,
            reprompt_rate_limiter=reprompt_rate_limiter,
            reprompts=reprompts,
            last_seen_coalescer=last_seen_coalescer,
            trusted_proxies=settings.trusted_proxies,
        )
        app.state.auth = auth_state

        # Phase 9: capture process start time + emit `app_started` audit
        # event. `/api/about` reads `app.state.started_at` directly and
        # queries the audit log for last_reboots.
        # Hex Audit BA-F4 (slice 8): drop the `contextlib.suppress` —
        # if the audit log can't take an `app_started` row at startup
        # the DB is broken and the app should refuse to boot. That is
        # the correct semantic for startup-time audit-write failure.
        #
        # Slice 8.5 (FG3-F1): pass `checkpoint=False`. If a prior
        # process left a dirty WAL pending after unclean shutdown, the
        # checkpoint can raise `OperationalError: database is locked`,
        # which under the new fatal-on-failure posture would create a
        # boot-loop (s6 restarts → same race → loop). The audit row
        # itself is durable in the WAL once the txn commits; the
        # retention loop's first 6 h tick (or its idle checkpoint at
        # the 24 h mark) handles WAL hygiene.
        app.state.started_at = datetime.now(tz=UTC)
        startup_audit = AuditLogRepository(sessionmaker_)
        await startup_audit.append_standalone(
            event_type="app_started",
            actor="system",
            data={"version": __version__, "build_sha": BUILD_SHA or "dev"},
            at=app.state.started_at,
            checkpoint=False,
        )

        bus = EventBus()
        ws_registry = WsRegistry()
        app.state.event_bus = bus
        app.state.ws_registry = ws_registry
        app.state.supervisor_tasks = set()

        # Production: real Supervisor + AsyncioProcessSpawner.
        # Test: caller passes a factory + optionally a FakeProcessSpawner.
        supervisor: Supervisor
        if supervisor_factory is not None:
            supervisor = supervisor_factory(
                sessionmaker=sessionmaker_,
                key_material=key_material,
                bus=bus,
            )
        else:
            ingest_key = await _read_ingest_key_now(sessionmaker_)
            ingest_url = f"rtmp://127.0.0.1:1935/live/{ingest_key}"
            spawner = process_spawner if process_spawner is not None else AsyncioProcessSpawner()
            # Host-stats sampler — owns one shared httpx client kept on
            # `app.state` so the lifespan can close it on shutdown. The
            # factory closure binds the just-constructed supervisor as
            # the `WorkerPidProvider` (the supervisor owns `_workers`).
            host_stats_client = httpx.AsyncClient()
            app.state.host_stats_client = host_stats_client

            def _make_host_stats_sampler(provider: WorkerPidProvider) -> HostStatsSampler:
                return HostStatsSampler(
                    proc_reader=ProcfsStatReader(),
                    ingest_fetcher=NginxRtmpStatFetcher(
                        url="http://127.0.0.1:8888/rtmp_stat",
                        client=host_stats_client,
                    ),
                    pid_provider=provider,
                )

            supervisor = Supervisor(
                sessionmaker=sessionmaker_,
                key_material=key_material,
                process_spawner=spawner,
                credential_registry=get_credential_registry(),
                ingest_loopback_url=ingest_url,
                # Slice 3 (Hex Audit FG2-H3): inject the same
                # `rotation_lock` the auth state uses, so start_run can
                # acquire it atomically with the OFFLINE pre-check.
                # Lock ordering rule: supervisor._lock BEFORE
                # rotation_lock — enforced at the two call sites
                # (`supervisor.start_run` here; rotate-passphrase via
                # `supervisor.acquire_state_lock()`).
                rotation_lock=auth_state.rotation_lock,
                worker_factory=worker_factory,
                host_stats_sampler_factory=_make_host_stats_sampler,
                # Hex Audit SA-F14 (slice 10): half-baked YouTube
                # primary+backup feature is gated off by default.
                # `RESTREAM_YOUTUBE_BACKUP_ENABLED=true` opts in.
                youtube_backup_enabled=settings.youtube_backup_enabled,
            )
        app.state.supervisor = supervisor

        async with asyncio.TaskGroup() as api_tg:
            sup_task = api_tg.create_task(supervisor.run(bus), name="supervisor")
            broadcaster_task = api_tg.create_task(
                ws_broadcaster(bus, ws_registry), name="ws-broadcaster"
            )
            pruner_task = api_tg.create_task(
                _reprompt_pruner_loop(reprompts), name="reprompt-pruner"
            )
            # Phase 9: 24-h cleanup loop for `.kdf_salt.previous`. Loop
            # checks every minute against the file's mtime + the 24-h
            # grace; benign in the (typical) case where no rotation
            # has happened — the file doesn't exist and the check
            # short-circuits.
            kdf_cleanup_task = api_tg.create_task(
                _kdf_salt_previous_cleanup_loop(
                    settings.kdf_salt_previous_path,
                    sessionmaker_,
                ),
                name="kdf-salt-previous-cleanup",
            )
            # Hex Audit BA-F1 (2026-05-18): periodic DB-side cleanup for
            # `settings.ingest_key_previous` after the grace window
            # expires. The auth webhooks consult `grace_until` directly
            # and refuse the previous key past expiry; this loop drops
            # the stale row state so the DB doesn't keep the old key
            # indefinitely. Idempotent — a tick during a non-rotation
            # window is a single `UPDATE ... WHERE ... < :now` with 0
            # matching rows.
            ingest_key_cleanup_task = api_tg.create_task(
                _ingest_key_previous_cleanup_loop(sessionmaker_),
                name="ingest-key-previous-cleanup",
            )
            # Hex Audit FG2-M6 (slice 8): periodic audit-log retention
            # + WAL checkpoint cadence. Replaces the per-append
            # `wal_checkpoint(TRUNCATE)` that the slice 8 transactional
            # refactor removed.
            audit_retention_task = api_tg.create_task(
                _audit_log_retention_loop(
                    sessionmaker_,
                    retention_days=settings.audit_log_retention_days,
                ),
                name="audit-log-retention",
            )
            try:
                await supervisor.wait_ready(timeout=LIFESPAN_SUPERVISOR_READY_TIMEOUT)
            except BaseException:
                sup_task.cancel()
                broadcaster_task.cancel()
                pruner_task.cancel()
                kdf_cleanup_task.cancel()
                ingest_key_cleanup_task.cancel()
                audit_retention_task.cancel()
                raise
            # Phase 10 §C: signal s6-ready regardless of KeyMaterial unlock
            # state — the supervisor is ready, uvicorn has bound its socket,
            # and that's what s6's "ready" contract means (`/livez` semantics,
            # not `/readyz`). Paste-mode locked still signals ready so s6
            # dependents don't block on human input that may never come.
            _notify_s6_ready()
            try:
                yield
            finally:
                with contextlib.suppress(Exception):
                    await supervisor.stop(grace=LIFESPAN_SHUTDOWN_GRACE)
                broadcaster_task.cancel()
                pruner_task.cancel()
                kdf_cleanup_task.cancel()
                ingest_key_cleanup_task.cancel()
                audit_retention_task.cancel()
                # Drain any pending fire-and-forget supervisor tasks
                # (run-start, run-stop). Hex Audit FG-F16 (slice 10):
                # explicitly cancel each pending task BEFORE awaiting,
                # so an outer cancellation of `wait_for` cannot leak
                # background tasks past the lifespan teardown. The
                # `bucket.discard` done-callback on each task mutates
                # the set; snapshot via `list(...)` to iterate safely.
                pending_snapshot: list[asyncio.Task[object]] = list(app.state.supervisor_tasks)
                if pending_snapshot:
                    for task in pending_snapshot:
                        if not task.done():
                            task.cancel()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            asyncio.gather(*pending_snapshot, return_exceptions=True),
                            timeout=LIFESPAN_SHUTDOWN_GRACE.total_seconds(),
                        )
        # Close the host-stats shared httpx client, if one was created
        # (test path uses `supervisor_factory` and never attaches one).
        host_stats_client_obj: httpx.AsyncClient | None = getattr(
            app.state, "host_stats_client", None
        )
        if host_stats_client_obj is not None:
            with contextlib.suppress(Exception):
                await host_stats_client_obj.aclose()

        # Engine dispose AFTER the TG closes so background DB users have
        # exited. KeyMaterial holds derived keys; the GC will reclaim
        # them on process exit. No explicit zeroization here — ADR-0006
        # names process-memory snapshotting as out of scope.
        with contextlib.suppress(Exception):
            await engine.dispose()

    return lifespan


async def _reprompt_pruner_loop(store: RepromptStore) -> None:
    """Periodic GC for `RepromptStore`. Bounded loop; exits on cancel."""
    try:
        while True:
            await asyncio.sleep(REPROMPT_PRUNE_INTERVAL_SECONDS)
            with contextlib.suppress(Exception):
                store.prune_expired()
    except asyncio.CancelledError:
        raise


async def _kdf_salt_previous_cleanup_once(
    typed_path: Path,
    sessionmaker_: _AsyncSessionmaker,
) -> None:
    """One iteration of the `.kdf_salt.previous` cleanup loop.

    Extracted from the loop so the audit-before-unlink invariant
    (Hex Audit FG2-H5, slice 7) is unit-testable. The loop body
    becomes a single call to this helper plus the sleep cadence.

    Sequence:
      1. Stat the file. Missing or sub-grace → return (no-op).
      2. Audit `kdf_salt_previous_cleared` with `path` + `mtime`.
         On audit failure → return WITHOUT unlinking. Next tick
         retries the whole sequence; the audit row is guaranteed
         once persistence recovers.
      3. Unlink. On failure → log warning. Next tick sees the file
         again and emits a duplicate audit; the `mtime` field
         disambiguates retries vs. distinct cleanups.
    """
    from app.repositories.audit_log import AuditLogRepository as _AuditRepo

    try:
        if not typed_path.exists():
            return
        mtime = datetime.fromtimestamp(typed_path.stat().st_mtime, tz=UTC)
        age = datetime.now(tz=UTC) - mtime
        if age < KDF_SALT_PREVIOUS_GRACE:
            return
    except FileNotFoundError:
        return
    except OSError as exc:
        _logger.warning(
            "kdf_salt_previous_cleanup_stat_failed",
            path=str(typed_path),
            exc_type=type(exc).__name__,
        )
        return
    # FG2-H5: audit BEFORE unlink. On audit failure, file stays on
    # disk and next tick retries — forensic record is guaranteed once
    # persistence recovers. Hex Audit BA-F4 (slice 8): uses the
    # standalone repo path — no caller session to merge into, and the
    # unlink that follows is a non-DB op that has nothing to share a
    # transaction with.
    try:
        audit = _AuditRepo(sessionmaker_)
        await audit.append_standalone(
            event_type="kdf_salt_previous_cleared",
            actor="system",
            data={"path": str(typed_path), "mtime": mtime.isoformat()},
        )
    except Exception:
        _logger.exception("kdf_salt_previous_cleanup_audit_failed")
        return
    try:
        typed_path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except OSError as exc:
        _logger.warning(
            "kdf_salt_previous_cleanup_unlink_failed",
            path=str(typed_path),
            exc_type=type(exc).__name__,
        )


async def _kdf_salt_previous_cleanup_loop(
    path: Path,
    sessionmaker_: _AsyncSessionmaker,
) -> None:
    """Phase 9: unlink `.kdf_salt.previous` 24 h after rotate-passphrase
    writes it.

    Loop wraps `_kdf_salt_previous_cleanup_once` with the sleep cadence.
    The audit-before-unlink ordering (Hex Audit FG2-H5, slice 7) lives
    in the inner helper so it can be unit-tested without driving the
    loop.

    Exits on cancel; never raises out of the loop.
    """
    try:
        while True:
            await asyncio.sleep(KDF_SALT_PREVIOUS_CHECK_INTERVAL_SECONDS)
            await _kdf_salt_previous_cleanup_once(path, sessionmaker_)
    except asyncio.CancelledError:
        raise


async def _ingest_key_previous_cleanup_loop(
    sessionmaker_: _AsyncSessionmaker,
) -> None:
    """Hex Audit BA-F1 (2026-05-18): clear `settings.ingest_key_previous`
    after `ingest_key_grace_until` has expired.

    The auth webhooks (`/internal/rtmp/publish`, `/internal/mtx/auth`)
    refuse the previous key once `grace_until` is past — that's the
    security boundary. This loop is the DB-side hygiene: it drops the
    stale columns so the row doesn't carry an unusable old key
    indefinitely (and so a subsequent operator-facing "is rotation
    grace active?" UI signal stays accurate).

    `SettingsRepository.clear_ingest_key_previous_after_grace` does
    the atomic `UPDATE ... WHERE grace_until < :now AND previous IS
    NOT NULL`, returning True only when a row was actually cleared.
    Two concurrent ticks racing produce at most one audit row because
    the second tick sees no matching rows.

    Loop exits on cancel; per-iteration failures are logged and the
    loop continues (matches `_kdf_salt_previous_cleanup_loop` posture).
    """
    from app.repositories.audit_log import AuditLogRepository as _AuditRepo
    from app.repositories.settings_repo import SettingsRepository as _SettingsRepo

    sm = sessionmaker_
    try:
        while True:
            await asyncio.sleep(INGEST_KEY_PREVIOUS_CHECK_INTERVAL_SECONDS)
            # Hex Audit BA-F4 (slice 8): clear + audit in ONE txn. The
            # prior two-session split could leave the row cleared with
            # no audit if the audit's separate session failed. Both
            # writes now commit atomically or neither does.
            try:
                async with sm() as session, session.begin():
                    cleared = await _SettingsRepo(session).clear_ingest_key_previous_after_grace(
                        now=datetime.now(tz=UTC),
                    )
                    if cleared:
                        audit = _AuditRepo(sm)
                        await audit.append(
                            session,
                            event_type="ingest_key_previous_cleared",
                            actor="system",
                        )
            except Exception as exc:
                _logger.warning(
                    "ingest_key_previous_cleanup_failed",
                    exc_type=type(exc).__name__,
                )
    except asyncio.CancelledError:
        raise


@dataclass(slots=True)
class _AuditRetentionTickState:
    """Mutable state passed across `_audit_log_retention_once` calls.

    Extracted (slice 8.5) to make per-tick semantics unit-testable.
    `consecutive_failures` and `last_idle_checkpoint` are the only
    cross-iteration state.
    """

    consecutive_failures: int = 0
    last_idle_checkpoint: datetime | None = None


async def _audit_log_retention_once(
    sessionmaker_: _AsyncSessionmaker,
    retention_days: int,
    state: _AuditRetentionTickState,
    *,
    now: datetime,
    idle_checkpoint_interval: timedelta = timedelta(hours=24),
) -> None:
    """Run one iteration of the audit-log retention cadence.

    Extracted from `_audit_log_retention_loop` for testability
    (slice 8.5, CR2-Re-F3 closure). The loop body's invariants:

    - **CR2-Re-F1**: each `run_wal_checkpoint` call is wrapped in its
      own `try/except`. A checkpoint failure doesn't escape this
      function; it counts toward `state.consecutive_failures`.
    - **CR2-Re-F2**: `state.last_idle_checkpoint` is updated ONLY on
      successful checkpoint, never on failure paths.
    - **BA2-F3 / FG3-F2**: on `state.consecutive_failures ==
      AUDIT_LOG_RETENTION_FAILURE_THRESHOLD`, emits a `_logger.critical`
      escalation. Successful ticks reset the counter to 0.
    - **BA2-F6**: emits `audit_log_retention_tick` at INFO on every
      successful tick (regardless of `deleted` count).
    - **FG3-F4**: retention markers (`audit_log_retention_run`) are
      excluded from the delete predicate.

    Never raises (except `asyncio.CancelledError`, which is not
    caught — the caller's `try/except CancelledError: raise` handles
    graceful shutdown).
    """
    from app.repositories.audit_log import AuditLogRepository as _AuditRepo

    sm = sessionmaker_
    repo = _AuditRepo(sm)
    cutoff = now - timedelta(days=retention_days)
    deleted = 0
    tick_failed = False
    checkpoint_ran = False

    try:
        async with sm() as session, session.begin():
            deleted = await repo.delete_older_than(
                session,
                cutoff=cutoff,
                exclude_event_types=AUDIT_LOG_RETENTION_EXCLUDED_EVENT_TYPES,
            )
            if deleted > 0:
                await repo.append(
                    session,
                    event_type="audit_log_retention_run",
                    actor="system",
                    data={
                        "rows_deleted": deleted,
                        "cutoff": cutoff.isoformat(),
                        "retention_days": retention_days,
                    },
                )
    except Exception as exc:
        tick_failed = True
        _logger.warning(
            "audit_log_retention_failed",
            exc_type=type(exc).__name__,
            consecutive_failures=state.consecutive_failures + 1,
        )

    if not tick_failed:
        if deleted > 0:
            try:
                await repo.run_wal_checkpoint(reason="audit_log_retention_run")
                state.last_idle_checkpoint = now
                checkpoint_ran = True
            except Exception as exc:
                tick_failed = True
                _logger.warning(
                    "audit_log_retention_checkpoint_failed",
                    exc_type=type(exc).__name__,
                    reason="audit_log_retention_run",
                    consecutive_failures=state.consecutive_failures + 1,
                )
        elif (
            state.last_idle_checkpoint is None
            or now - state.last_idle_checkpoint >= idle_checkpoint_interval
        ):
            try:
                await repo.run_wal_checkpoint(reason="audit_log_idle_checkpoint")
                state.last_idle_checkpoint = now
                checkpoint_ran = True
            except Exception as exc:
                tick_failed = True
                _logger.warning(
                    "audit_log_retention_checkpoint_failed",
                    exc_type=type(exc).__name__,
                    reason="audit_log_idle_checkpoint",
                    consecutive_failures=state.consecutive_failures + 1,
                )

    if tick_failed:
        state.consecutive_failures += 1
        if state.consecutive_failures == AUDIT_LOG_RETENTION_FAILURE_THRESHOLD:
            _logger.critical(
                "audit_log_retention_stuck",
                consecutive_failures=state.consecutive_failures,
                threshold=AUDIT_LOG_RETENTION_FAILURE_THRESHOLD,
                note=(
                    "audit-log retention loop has failed for "
                    f"{state.consecutive_failures} consecutive ticks "
                    f"(~{state.consecutive_failures * 6} h). DB is "
                    "likely degraded — disk full, schema "
                    "corruption, permission flip. Investigate "
                    "immediately; retention floor is NOT being "
                    "enforced."
                ),
            )
    else:
        if state.consecutive_failures > 0:
            _logger.info(
                "audit_log_retention_recovered",
                prior_consecutive_failures=state.consecutive_failures,
            )
        state.consecutive_failures = 0
        _logger.info(
            "audit_log_retention_tick",
            rows_deleted=deleted,
            cutoff=cutoff.isoformat(),
            retention_days=retention_days,
            checkpoint_ran=checkpoint_ran,
        )


async def _audit_log_retention_loop(
    sessionmaker_: _AsyncSessionmaker,
    retention_days: int,
) -> None:
    """Hex Audit FG2-M6 (slice 8) + slice 8.5 hardening + slice 10
    FG3-F9 (boot-time idle checkpoint).

    Every 6 h, calls `_audit_log_retention_once(...)`. The single-
    iteration body is extracted into the helper above so the
    invariants (checkpoint-failure isolation, last_idle_checkpoint
    rollback on failure, consecutive-failure escalation) are
    unit-testable without driving the loop's sleep cadence.

    Hex Audit FG3-F9 (slice 10): run one retention tick on entry
    BEFORE the first sleep. Otherwise `state.last_idle_checkpoint`
    starts as `None` and an operator restart cycle shorter than 6 h
    (a deployment iteration on Unraid is typically ≤ 5 min) means
    the idle checkpoint never runs and WAL grows unbounded on
    read-heavy deployments with zero audit churn. The boot tick is
    a single delete (likely 0 rows on a fresh boot) plus one WAL
    checkpoint — cheap and bounds the worst case.

    Exits on cancel.
    """
    state = _AuditRetentionTickState()
    try:
        # FG3-F9: prime the loop on boot so a restart-heavy
        # deployment still triggers WAL TRUNCATE at least once
        # per process lifetime.
        await _audit_log_retention_once(
            sessionmaker_,
            retention_days,
            state,
            now=datetime.now(tz=UTC),
        )
        while True:
            await asyncio.sleep(AUDIT_LOG_RETENTION_CHECK_INTERVAL_SECONDS)
            await _audit_log_retention_once(
                sessionmaker_,
                retention_days,
                state,
                now=datetime.now(tz=UTC),
            )
    except asyncio.CancelledError:
        raise


def _notify_s6_ready() -> None:
    """Phase 10 §M: write the s6-overlay "service ready" signal.

    s6's `notification-fd` mechanism reads from `S6_NOTIFICATION_FD`
    (fd 3). The contract is: write a newline, then **close** the fd —
    the close lets s6 see EOF on the pipe and prevents the open fd
    from being inherited by every subsequent ffmpeg child the
    supervisor spawns (which would leak into `/proc/<ffmpeg-pid>/fd/3`
    and confuse s6's diagnostics if the child happened to write to
    fd 3). Reviewer C1 (Phase 10 review): without the close, fd 3
    survives across `subprocess.Popen` calls because Python's default
    is `close_fds=True` only for fds it knows about — fd 3 from
    `os.write` falls outside that scope on some platforms.

    In dev / test / `python -m app.main` runs outside an s6
    supervision tree, fd 3 isn't open and `os.write` raises `OSError`
    (EBADF) — swallowed; this function is a no-op in that case.

    Called exactly once per lifespan, right after
    `supervisor.wait_ready()` returns successfully and BEFORE `yield`.
    """
    try:
        os.write(S6_NOTIFICATION_FD, b"\n")
        os.close(S6_NOTIFICATION_FD)
    except OSError:
        # fd 3 not open (dev / test) — nothing to write or close.
        return


async def _read_ingest_key_now(sessionmaker_: _AsyncSessionmaker) -> str:
    """Read `settings.ingest_key_current` once at lifespan start.

    Mid-run rotation of the ingest key takes effect on the next process
    restart — consistent with ADR-0001 §"no live reload" and ADR-0003
    §"Ingest key rotation" (the grace window lets OBS keep streaming
    with the previous key until the operator updates it).
    """
    from app.repositories.settings_repo import SettingsRepository

    async with sessionmaker_() as session:
        s = await SettingsRepository(session).get()
        return s.ingest_key_current


def main() -> None:
    """uvicorn entrypoint — `python -m app.main`."""
    import uvicorn

    settings = AppSettings()
    uvicorn.run(
        "app.main:create_app",
        host=settings.bind_host,
        port=settings.bind_port,
        factory=True,
        log_config=None,  # structlog handles formatting; uvicorn defaults clobber.
        access_log=False,
        # Phase 10 §M / ADR-0001 — LOAD-BEARING. Multiple workers would
        # each hold their own KeyMaterial (passphrase unlock per worker),
        # each spawn a Supervisor TaskGroup (multiple ffmpeg fan-outs of
        # the same RTMP publish), each call `on_publish` (duplicate audit
        # rows + WS broadcasts). Every invariant in ADRs 0003-0011
        # assumes a single process. Concurrency lives in asyncio, not in
        # uvicorn workers. Do NOT bump this without amending ADR-0001.
        workers=1,
    )


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["create_app", "main"]
