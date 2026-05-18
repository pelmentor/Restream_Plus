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
    from app.fanout.supervisor import WorkerFactory

    SupervisorFactory = Callable[..., Supervisor]

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

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(request: Request, full_path: str) -> FileResponse:
        if any(full_path.startswith(p) for p in api_prefixes):
            raise HTTPException(status_code=404)
        # Real on-disk file → serve it (handles /assets/*, favicon, etc.).
        # `resolve()` collapses `..` segments to defend against path
        # traversal attempting to read files outside spa_dir.
        candidate = (spa_dir / full_path).resolve()
        try:
            candidate.relative_to(spa_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404) from None
        if candidate.is_file():
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
        reprompts = RepromptStore()

        auth_state = AuthState(
            key_material=key_material,
            sessionmaker=sessionmaker_,
            rate_limiter=rate_limiter,
            unlock_rate_limiter=unlock_rate_limiter,
            reprompts=reprompts,
            trusted_proxies=settings.trusted_proxies,
        )
        app.state.auth = auth_state

        # Phase 9: capture process start time + emit `app_started` audit
        # event. `/api/about` reads `app.state.started_at` directly and
        # queries the audit log for last_reboots.
        app.state.started_at = datetime.now(tz=UTC)
        with contextlib.suppress(Exception):
            startup_audit = AuditLogRepository(sessionmaker_)
            await startup_audit.append(
                event_type="app_started",
                actor="system",
                data={"version": __version__, "build_sha": BUILD_SHA or "dev"},
                at=app.state.started_at,
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
                worker_factory=worker_factory,
                host_stats_sampler_factory=_make_host_stats_sampler,
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
            try:
                await supervisor.wait_ready(timeout=LIFESPAN_SUPERVISOR_READY_TIMEOUT)
            except BaseException:
                sup_task.cancel()
                broadcaster_task.cancel()
                pruner_task.cancel()
                kdf_cleanup_task.cancel()
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
                # Drain any pending fire-and-forget supervisor tasks
                # (run-start, run-stop). Each is bounded by the
                # supervisor's own timeouts; we wait at most a few
                # seconds in aggregate.
                pending: set[asyncio.Task[object]] = app.state.supervisor_tasks
                if pending:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
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


async def _kdf_salt_previous_cleanup_loop(
    path: object,
    sessionmaker_: object,
) -> None:
    """Phase 9: unlink `.kdf_salt.previous` 24 h after rotate-passphrase
    writes it.

    Loop checks the file's mtime every minute. When the mtime is
    >24 h old, unlinks the file and emits one
    `kdf_salt_previous_cleared` audit event. Benign when no rotation
    has happened — the file doesn't exist and the check short-circuits.

    Exits on cancel; never raises out of the loop (per-iteration
    contextlib.suppress guards file-system races against a concurrent
    rotate that overwrites the file).
    """
    from pathlib import Path  # local import — keeps the module import graph small

    from app.repositories.audit_log import AuditLogRepository as _AuditRepo

    typed_path = path if isinstance(path, Path) else Path(str(path))
    try:
        while True:
            await asyncio.sleep(KDF_SALT_PREVIOUS_CHECK_INTERVAL_SECONDS)
            # Reviewer M-4: narrow the suppress so PermissionError / OSError
            # / audit failures surface as warnings instead of being silently
            # swallowed. FileNotFoundError on the stat/unlink path is the
            # only condition that's legitimately ignorable (race with a
            # concurrent rotate that already cleared the file).
            try:
                if not typed_path.exists():
                    continue
                mtime = datetime.fromtimestamp(typed_path.stat().st_mtime, tz=UTC)
                age = datetime.now(tz=UTC) - mtime
                if age < KDF_SALT_PREVIOUS_GRACE:
                    continue
                typed_path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except OSError as exc:
                _logger.warning(
                    "kdf_salt_previous_cleanup_failed",
                    path=str(typed_path),
                    exc_type=type(exc).__name__,
                )
                continue
            try:
                audit = _AuditRepo(sessionmaker_)  # type: ignore[arg-type]
                await audit.append(
                    event_type="kdf_salt_previous_cleared",
                    actor="system",
                )
            except Exception:
                _logger.exception("kdf_salt_previous_cleanup_audit_failed")
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


async def _read_ingest_key_now(sessionmaker_: object) -> str:
    """Read `settings.ingest_key_current` once at lifespan start.

    Mid-run rotation of the ingest key takes effect on the next process
    restart — consistent with ADR-0001 §"no live reload" and ADR-0003
    §"Ingest key rotation" (the grace window lets OBS keep streaming
    with the previous key until the operator updates it).
    """
    from app.repositories.settings_repo import SettingsRepository

    async with sessionmaker_() as session:  # type: ignore[operator]
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
