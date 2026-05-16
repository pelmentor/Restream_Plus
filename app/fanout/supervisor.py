"""Supervisor — top-level orchestrator for Phase 5.

Owns:

- The system-wide `RunState` (OFFLINE/STARTING/ARMED/LIVE/STOPPING/ERROR).
- The map of in-memory `Target` aggregates (Phase 4 domain), keyed by
  target id, kept in sync with the DB on every state change.
- The map of running `FFmpegWorker` instances, keyed by `WorkerId`.
- The per-worker event-pump tasks that drain `worker.events()`, project
  to `WorkerSnapshot`, ingest into `Target`, and publish `BusEvent`s.
- The plaintext-stream-key registration in `CredentialRegistry` for the
  lifetime of each worker (defence-in-depth alongside `RedactionSink`).
- The 1-Hz heartbeat for `/readyz` (Phase 6 reads it).
- The two-TaskGroup shutdown choreography from ADR-0003: SIGTERM →
  STOPPING → per-worker SIGTERM → grace → SIGKILL → audit row.

Configuration discovery is **event-driven, not poll-driven**: REST
handlers (Phase 6) call `enable_target` / `disable_target` /
`reset_target_worker` after committing their DB writes, and the
supervisor re-reads the DB inside those calls. There is no background
poll loop — the DB is the source of truth, the REST handler is the
trigger.

VK per-session credentials are deleted from the DB at end of run (the
supervisor iterates targets whose type's `credential_lifetime` is
`PER_SESSION` and calls `CredentialsRepository.delete_for_target`),
followed by an audit row.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.key_material import KeyMaterial
from app.crypto.aead import build_credential_aad, decrypt
from app.domain.credential_policy import CredentialLifetime, lifetime_for
from app.domain.errors import IllegalRunStateTransitionError
from app.domain.health import aggregate_target_health
from app.domain.run_state import RunAction, RunState
from app.domain.run_state import transition as run_transition
from app.domain.target import Target, TargetUiState
from app.domain.target_types import TargetType
from app.domain.worker_state import WorkerRole, WorkerSnapshot, WorkerState
from app.fanout.event_bus import (
    BusEvent,
    DropAlertEvent,
    EventBus,
    RunStateChangedEvent,
    TargetSnapshotEvent,
)
from app.fanout.ffmpeg_urls import compose_out_url
from app.fanout.ffmpeg_worker import FFmpegWorker
from app.fanout.process import ProcessSpawner
from app.fanout.worker import WorkerEvent, WorkerId, WorkerSpec, WorkerStateEvent
from app.logging_setup import CredentialRegistry
from app.repositories._converters import target_dto_to_domain
from app.repositories.audit_log import AuditLogRepository
from app.repositories.credentials import CredentialsRepository
from app.repositories.sessions_history import SessionsHistoryRepository
from app.repositories.targets import TargetDTO, TargetsRepository

_logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL: Final[float] = 1.0
"""1-Hz floor on heartbeat updates. Reconciler-driven updates also fire
on every state change, so the floor only matters when the supervisor
is genuinely idle."""

HEARTBEAT_STALE_AFTER: Final[timedelta] = timedelta(seconds=10)
"""ADR-0011 threshold for `/readyz` to consider the supervisor wedged.
Phase 6's `/readyz` handler compares this against `heartbeat_age()`."""

WORKER_GRACE: Final[timedelta] = timedelta(seconds=5)
"""Per-worker SIGTERM-to-SIGKILL window during stop_run. Per ADR-0003."""

DROP_ALERT_INTERVAL_SECONDS: Final[float] = 5.0
"""Min time between bus DropAlertEvent emissions, so a sustained drop
storm doesn't itself flood the bus."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class WorkerFactory(Protocol):
    """Constructor signature for the FFmpegWorker injection point.

    Tests substitute a factory that wires deterministic clocks /
    breaker policy / RNG without changing the supervisor's call site.
    """

    def __call__(self, *, spec: WorkerSpec) -> FFmpegWorker: ...


class Supervisor:
    """Top-level fan-out orchestrator. Single instance per process."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        key_material: KeyMaterial,
        process_spawner: ProcessSpawner,
        credential_registry: CredentialRegistry,
        ingest_loopback_url: str,
        clock: Callable[[], datetime] = _utc_now,
        monotonic_clock_ns: Callable[[], int] = time.monotonic_ns,
        worker_factory: WorkerFactory | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._key_material = key_material
        self._process_spawner = process_spawner
        self._credential_registry = credential_registry
        self._ingest_loopback_url = ingest_loopback_url
        self._clock = clock
        self._monotonic_clock_ns = monotonic_clock_ns
        self._worker_factory = worker_factory or self._default_worker_factory

        self._run_state: RunState = RunState.OFFLINE
        # Wall-clock timestamp of the most recent `_run_state` transition.
        # None until the first transition. Surfaced to the Phase 8 frontend
        # via `RunStateView.run_state_changed_at` so a hard-reload mid-LIVE
        # picks up the timer correctly (see phase-8-design-memo §A).
        self._run_state_changed_at: datetime | None = None
        self._targets: dict[str, Target] = {}
        self._workers: dict[WorkerId, FFmpegWorker] = {}
        self._registered_secrets: dict[WorkerId, str] = {}
        self._target_to_workers: dict[str, set[WorkerId]] = {}
        self._current_run_id: str | None = None

        self._bus: EventBus | None = None
        self._super_tg: asyncio.TaskGroup | None = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._heartbeat_ns: int = monotonic_clock_ns()
        self._last_drop_alert_ns: int = 0
        self._last_drop_alert_total: int = 0
        self._lock = asyncio.Lock()
        # Strong-reference set for fire-and-forget bus-notify tasks
        # so they aren't GC'd before completion (Python's task ref is weak).
        self._pending_notifies: set[asyncio.Task[None]] = set()
        # Pending audit-row writes for credential decrypt failures —
        # written outside the open DB session in `_load_runnable_targets`
        # to avoid SQLite write-lock contention.
        self._decrypt_failures: list[str] = []

    # ------------------------------------------------------------------
    # Lifecycle — owned by the FastAPI lifespan
    # ------------------------------------------------------------------

    async def run(self, bus: EventBus) -> None:
        """Run until `stop()`. Owns the supervisor TaskGroup.

        The lifespan (Phase 6) creates the bus, constructs this object,
        and awaits `run(bus)` inside the API TaskGroup so a supervisor
        crash propagates to the API and triggers ASGI shutdown.

        TaskGroup teardown: when `_stop_event` fires we explicitly
        cancel the long-running infrastructure tasks (heartbeat,
        drop-alert) so the TG can exit. Worker pump tasks added via
        `enable_target` exit naturally because `worker.stop()` closes
        their event channels.
        """
        self._bus = bus
        self._heartbeat_ns = self._monotonic_clock_ns()
        await self._audit("supervisor_started", data={"build_at": self._clock().isoformat()})
        try:
            async with asyncio.TaskGroup() as tg:
                self._super_tg = tg
                heartbeat = tg.create_task(self._heartbeat_loop(), name="supervisor-heartbeat")
                drop_alert = tg.create_task(self._drop_alert_loop(), name="supervisor-drop-alerts")
                self._ready_event.set()
                await self._stop_event.wait()
                heartbeat.cancel()
                drop_alert.cancel()
        finally:
            self._super_tg = None

    async def wait_ready(self, timeout: float) -> None:  # noqa: ASYNC109 — public idiom; matches asyncio.wait_for shape
        """Block until `run()` has entered its TaskGroup. Lifespan calls
        this before yielding so `/readyz` can return 200 immediately."""
        try:
            async with asyncio.timeout(timeout):
                await self._ready_event.wait()
        except TimeoutError as exc:
            raise RuntimeError("supervisor failed to become ready in time") from exc

    async def stop(self, grace: timedelta) -> None:
        """Drain workers and exit `run()`.

        Sequence:
          1. If a run is active, call `stop_run(grace)` to drain workers.
          2. Set `_stop_event` so the supervisor TG exits.
          3. The TG's `__aexit__` cancels heartbeat / drop-alert tasks.
        """
        if self._run_state not in (RunState.OFFLINE, RunState.ERROR):
            with contextlib.suppress(Exception):
                await self.stop_run(grace=grace, reason="user_stop")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    async def start_run(self) -> None:
        """OFFLINE → STARTING → (workers spawn) → ARMED.

        Reads enabled targets and their active credentials from the DB,
        decrypts each credential under the KEK, registers the plaintext
        in `CredentialRegistry`, composes the output URL, and spawns one
        FFmpegWorker per (target, role). On any error during spawn:
        rollback (stop already-started workers) and transition to ERROR.
        """
        async with self._lock:
            if self._run_state is not RunState.OFFLINE:
                raise IllegalRunStateTransitionError(
                    state=self._run_state, action=RunAction.START_REQUESTED
                )
            await self._set_run_state(
                RunState.STARTING,
                RunAction.START_REQUESTED,
                cause="user pressed START",
            )

            try:
                await self._open_run_session()
                pairs = await self._load_runnable_targets()
                # Flush any decrypt failure audit rows now that the
                # _load_runnable_targets session has closed (the audit
                # repo opens its own session; running it inside would
                # contend on the SQLite write lock).
                while self._decrypt_failures:
                    failed_id = self._decrypt_failures.pop(0)
                    with contextlib.suppress(Exception):
                        await self._audit(
                            "credential_decrypt_failed",
                            target_id=failed_id,
                            data={"reason": "aead_authentication_failed_or_kek_mismatch"},
                        )
                self._targets = {t.id: dom for (t, dom, _) in pairs}
                self._target_to_workers = {t.id: set() for (t, _, _) in pairs}
                for t_dto, t_dom, cred_plaintext in pairs:
                    if not t_dom.enabled:
                        continue
                    if cred_plaintext is None:
                        continue
                    await self._spawn_workers_for_target(
                        target_dto=t_dto,
                        target_dom=t_dom,
                        plaintext_key=cred_plaintext,
                    )
                await self._set_run_state(
                    RunState.ARMED,
                    RunAction.WORKERS_SPAWNED,
                    cause="all enabled targets have running workers",
                )
                await self._audit(
                    "run_started",
                    data={
                        "run_id": self._current_run_id,
                        "target_count": len(self._target_to_workers),
                        "worker_count": len(self._workers),
                    },
                )
            except BaseException:
                # Spawn failed; tear down anything we did spawn and
                # transition to ERROR so the user must clear it.
                with contextlib.suppress(Exception):
                    await self._tear_down_all_workers(grace=WORKER_GRACE)
                with contextlib.suppress(Exception):
                    await self._set_run_state(
                        RunState.ERROR,
                        RunAction.UNRECOVERABLE_FAILED,
                        cause="failure during start_run",
                    )
                with contextlib.suppress(Exception):
                    await self._close_run_session(reason="error")
                raise

    async def stop_run(self, *, grace: timedelta = WORKER_GRACE, reason: str = "user_stop") -> None:
        """Active state → STOPPING → (drain workers) → OFFLINE.

        Reason is one of `app.repositories.sessions_history.VALID_END_REASONS`.
        VK per-session credentials are deleted before the run-session row
        closes — this is the single owner of the lifecycle. Each cleanup
        step is best-effort so a teardown failure cannot leave a VK key
        in the DB or skip the audit (post-Phase-5 review fix).
        """
        async with self._lock:
            # STOPPING is a no-op (already in progress); OFFLINE/ERROR
            # are terminal-pending-clear. Without the STOPPING guard the
            # transition table refuses STOPPING→STOP_REQUESTED and the
            # run sticks halfway through teardown.
            if self._run_state in (RunState.OFFLINE, RunState.STOPPING, RunState.ERROR):
                return
            await self._set_run_state(
                RunState.STOPPING,
                RunAction.STOP_REQUESTED,
                cause=f"stop requested ({reason})",
            )
            teardown_exc: BaseException | None = None
            try:
                await self._tear_down_all_workers(grace=grace)
            except BaseException as exc:
                _logger.exception("supervisor_teardown_failed")
                teardown_exc = exc
            # Wipe and close MUST run on every path — VK keys are
            # single-use; a leaked row violates platform contract.
            with contextlib.suppress(Exception):
                await self._wipe_per_session_credentials()
            with contextlib.suppress(Exception):
                await self._close_run_session(reason=reason)
            with contextlib.suppress(Exception):
                await self._set_run_state(
                    RunState.OFFLINE,
                    RunAction.WORKERS_DRAINED,
                    cause="all workers drained",
                )
            with contextlib.suppress(Exception):
                await self._audit("run_stopped", data={"reason": reason})
            if teardown_exc is not None:
                raise teardown_exc

    # ------------------------------------------------------------------
    # Per-target control (Phase 6 REST handlers call these)
    # ------------------------------------------------------------------

    async def enable_target(self, target_id: str) -> None:
        """Spawn workers for `target_id` if RunState is active."""
        if self._run_state not in (RunState.STARTING, RunState.ARMED, RunState.LIVE):
            return
        async with self._lock:
            t_dto, t_dom, cred_plaintext = await self._reload_target(target_id)
            if t_dto is None or t_dom is None:
                return
            self._targets[t_dom.id] = t_dom
            self._target_to_workers.setdefault(t_dom.id, set())
            if not t_dom.enabled or cred_plaintext is None:
                return
            await self._spawn_workers_for_target(
                target_dto=t_dto,
                target_dom=t_dom,
                plaintext_key=cred_plaintext,
            )

    async def disable_target(self, target_id: str) -> None:
        """Stop workers for `target_id` (no-op if none running)."""
        async with self._lock:
            await self._tear_down_workers_for_target(target_id, grace=WORKER_GRACE)

    async def reset_target_worker(self, target_id: str, role: WorkerRole) -> None:
        """User clicked 'Retry now' on a FAILED_OPEN tile."""
        worker = self._workers.get(WorkerId(target_id=target_id, role=role))
        if worker is None:
            return
        await worker.user_reset()

    # ------------------------------------------------------------------
    # nginx-rtmp webhook signals (Phase 6 wires the HTTP route)
    # ------------------------------------------------------------------

    def notify_obs_publish_began(self) -> None:
        """ARMED → LIVE on first OBS publish."""
        if self._run_state is not RunState.ARMED:
            return
        try:
            self._run_state = run_transition(self._run_state, RunAction.OBS_PUBLISH_BEGAN)
        except IllegalRunStateTransitionError:
            return
        self._publish_run_state(RunState.ARMED, "OBS started publishing")

    def notify_obs_publish_ended(self) -> None:
        """LIVE → ARMED on OBS publish end (per ADR-0003 idle-timeout
        is upstream of this; this signal is for the immediate transition)."""
        if self._run_state is not RunState.LIVE:
            return
        try:
            self._run_state = run_transition(self._run_state, RunAction.OBS_PUBLISH_ENDED)
        except IllegalRunStateTransitionError:
            return
        self._publish_run_state(RunState.LIVE, "OBS stopped publishing")

    # ------------------------------------------------------------------
    # Read surface
    # ------------------------------------------------------------------

    def run_state(self) -> RunState:
        return self._run_state

    def run_state_changed_at(self) -> datetime | None:
        return self._run_state_changed_at

    def targets(self) -> tuple[Target, ...]:
        return tuple(self._targets.values())

    def heartbeat_age(self) -> timedelta:
        elapsed_ns = self._monotonic_clock_ns() - self._heartbeat_ns
        return timedelta(microseconds=elapsed_ns / 1000)

    def heartbeat_is_stale(self) -> bool:
        return self.heartbeat_age() > HEARTBEAT_STALE_AFTER

    def recent_log_lines(
        self, target_id: str, role: WorkerRole = WorkerRole.PRIMARY
    ) -> tuple[bytes, ...]:
        worker = self._workers.get(WorkerId(target_id=target_id, role=role))
        if worker is None:
            return ()
        return worker.recent_log_lines()

    # ------------------------------------------------------------------
    # Internal: spawning + tearing down workers
    # ------------------------------------------------------------------

    async def _spawn_workers_for_target(
        self,
        *,
        target_dto: TargetDTO,
        target_dom: Target,
        plaintext_key: str,
    ) -> None:
        roles = self._roles_for(target_dom)
        for role in roles:
            worker_id = WorkerId(target_id=target_dom.id, role=role)
            if worker_id in self._workers:
                continue
            output_url = compose_out_url(
                target_type=target_dom.type,
                target_url=target_dto.url,
                plaintext_key=plaintext_key,
                role=role,
            )
            # Register the secret AFTER URL composition; if anything in
            # the spawn path raises, unregister cleanly so the registry
            # doesn't accumulate orphan plaintext entries
            # (post-Phase-5 review fix H1).
            self._credential_registry.register(plaintext_key)
            self._registered_secrets[worker_id] = plaintext_key
            try:
                spec = WorkerSpec(
                    worker_id=worker_id,
                    target_type=target_dom.type,
                    input_url=self._ingest_loopback_url,
                    output_url=output_url,
                )
                worker = self._worker_factory(spec=spec)
                self._workers[worker_id] = worker
                self._target_to_workers.setdefault(target_dom.id, set()).add(worker_id)
                await worker.start()
                assert self._super_tg is not None
                self._super_tg.create_task(
                    self._worker_event_pump(worker_id),
                    name=f"worker-pump:{target_dom.id}:{role.value}",
                )
            except BaseException:
                self._unregister_secret(worker_id)
                self._workers.pop(worker_id, None)
                self._target_to_workers.get(target_dom.id, set()).discard(worker_id)
                raise

    def _default_worker_factory(self, *, spec: WorkerSpec) -> FFmpegWorker:
        return FFmpegWorker(
            spec=spec,
            process_spawner=self._process_spawner,
            clock=self._clock,
            monotonic_clock_ns=self._monotonic_clock_ns,
        )

    @staticmethod
    def _roles_for(t: Target) -> tuple[WorkerRole, ...]:
        if t.type is TargetType.YOUTUBE and t.backup_enabled:
            return (WorkerRole.PRIMARY, WorkerRole.BACKUP)
        return (WorkerRole.PRIMARY,)

    async def _tear_down_workers_for_target(self, target_id: str, *, grace: timedelta) -> None:
        worker_ids = list(self._target_to_workers.get(target_id, set()))
        for wid in worker_ids:
            worker = self._workers.get(wid)
            if worker is None:
                continue
            with contextlib.suppress(Exception):
                await worker.stop(grace=grace)
            self._unregister_secret(wid)
            self._workers.pop(wid, None)
            self._target_to_workers.get(target_id, set()).discard(wid)

    async def _tear_down_all_workers(self, *, grace: timedelta) -> None:
        worker_ids = list(self._workers.keys())
        # Run stops concurrently; each is bounded by `grace + _HARD_KILL_GRACE`.
        if worker_ids:
            async with asyncio.TaskGroup() as tg:
                for wid in worker_ids:
                    worker = self._workers[wid]
                    tg.create_task(worker.stop(grace=grace))
        for wid in worker_ids:
            self._unregister_secret(wid)
            self._workers.pop(wid, None)
            for ws in self._target_to_workers.values():
                ws.discard(wid)

    def _unregister_secret(self, worker_id: WorkerId) -> None:
        secret = self._registered_secrets.pop(worker_id, None)
        if secret is not None:
            self._credential_registry.unregister(secret)

    # ------------------------------------------------------------------
    # Internal: DB read / write
    # ------------------------------------------------------------------

    async def _load_runnable_targets(
        self,
    ) -> list[tuple[TargetDTO, Target, str | None]]:
        derived = self._key_material.require_ready()
        async with self._sessionmaker() as session:
            targets_repo = TargetsRepository(session)
            creds_repo = CredentialsRepository(session)
            enabled = await targets_repo.list_enabled()
            results: list[tuple[TargetDTO, Target, str | None]] = []
            for t_dto in enabled:
                cred = await creds_repo.get_for_target(t_dto.id)
                t_dom = target_dto_to_domain(t_dto, cred)
                plaintext: str | None = None
                if cred is not None:
                    aad = build_credential_aad(cred.salt)
                    try:
                        plaintext_bytes = decrypt(
                            derived.stream_key_kek,
                            cred.nonce,
                            cred.ciphertext,
                            aad,
                        )
                    except Exception:
                        _logger.exception(
                            "credential_decrypt_failed",
                            target_id=t_dto.id,
                        )
                        # An audit row makes a tampering / KEK-mismatch
                        # event first-class rather than only a log line
                        # (post-Phase-5 review fix L3). Skip the target;
                        # supervisor proceeds with the rest.
                        self._decrypt_failures.append(t_dto.id)
                        plaintext = None
                    else:
                        plaintext = plaintext_bytes.decode("utf-8", errors="strict")
                results.append((t_dto, t_dom, plaintext))
            return results

    async def _reload_target(
        self, target_id: str
    ) -> tuple[TargetDTO | None, Target | None, str | None]:
        derived = self._key_material.require_ready()
        async with self._sessionmaker() as session:
            targets_repo = TargetsRepository(session)
            creds_repo = CredentialsRepository(session)
            t_dto = await targets_repo.get_by_id(target_id)
            if t_dto is None:
                return None, None, None
            cred = await creds_repo.get_for_target(target_id)
            t_dom = target_dto_to_domain(t_dto, cred)
            plaintext: str | None = None
            if cred is not None:
                aad = build_credential_aad(cred.salt)
                try:
                    plaintext_bytes = decrypt(
                        derived.stream_key_kek,
                        cred.nonce,
                        cred.ciphertext,
                        aad,
                    )
                except Exception:
                    _logger.exception("credential_decrypt_failed", target_id=target_id)
                else:
                    plaintext = plaintext_bytes.decode("utf-8", errors="strict")
            return t_dto, t_dom, plaintext

    async def _wipe_per_session_credentials(self) -> None:
        per_session_targets: list[Target] = [
            t
            for t in self._targets.values()
            if lifetime_for(t.type) is CredentialLifetime.PER_SESSION
        ]
        if not per_session_targets:
            return
        # Phase 1: delete inside a single short transaction.
        cleared_target_ids: list[str] = []
        async with self._sessionmaker() as session, session.begin():
            creds_repo = CredentialsRepository(session)
            for t in per_session_targets:
                if await creds_repo.delete_for_target(t.id):
                    cleared_target_ids.append(t.id)
        # Phase 2: audit AFTER the wipe transaction commits. Audit
        # uses its own short-lived transaction; running it inside the
        # wipe txn would deadlock on the SQLite write lock.
        for tid in cleared_target_ids:
            await self._audit("vk_session_credential_cleared", target_id=tid)

    async def _open_run_session(self) -> None:
        async with self._sessionmaker() as session, session.begin():
            history = SessionsHistoryRepository(session)
            row = await history.start(notes={"trigger": "user_start"})
            self._current_run_id = row.id

    async def _close_run_session(self, *, reason: str) -> None:
        if self._current_run_id is None:
            return
        run_id = self._current_run_id
        self._current_run_id = None
        async with self._sessionmaker() as session, session.begin():
            history = SessionsHistoryRepository(session)
            await history.end(run_id, end_reason=reason)

    async def _audit(
        self,
        event_type: str,
        *,
        target_id: str | None = None,
        data: Mapping[str, object] | None = None,
    ) -> None:
        repo = AuditLogRepository(self._sessionmaker)
        await repo.append(
            event_type=event_type,
            target_id=target_id,
            data=dict(data) if data is not None else None,
        )

    # ------------------------------------------------------------------
    # Internal: per-worker event pump
    # ------------------------------------------------------------------

    async def _worker_event_pump(self, worker_id: WorkerId) -> None:
        worker = self._workers.get(worker_id)
        if worker is None:
            return
        try:
            async for event in worker.events():
                self._update_heartbeat()
                await self._handle_worker_event(worker_id, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception(
                "worker_event_pump_error",
                worker_id=f"{worker_id.target_id}:{worker_id.role.value}",
            )

    async def _handle_worker_event(self, worker_id: WorkerId, event: WorkerEvent) -> None:
        worker = self._workers.get(worker_id)
        target = self._targets.get(worker_id.target_id)
        if worker is None or target is None:
            return
        snapshot: WorkerSnapshot = worker.snapshot()
        target.ingest_snapshot(snapshot)
        ui_state = target.ui_state()
        snapshots_tuple = tuple(
            s
            for r in target.expected_worker_roles()
            if (s := target.worker_set.role(r)) is not None
        )
        await self._publish_target_snapshot(target.id, ui_state, snapshots_tuple)
        if (
            event.kind == "state"
            and isinstance(event.payload, WorkerStateEvent)
            and event.payload.new_state is WorkerState.FAILED_OPEN
        ):
            with contextlib.suppress(Exception):
                await self._audit(
                    "worker_failed_open",
                    target_id=worker_id.target_id,
                    data={
                        "role": worker_id.role.value,
                        "cause": event.payload.cause,
                    },
                )

    # ------------------------------------------------------------------
    # Internal: bus publishing
    # ------------------------------------------------------------------

    async def _set_run_state(
        self,
        new_state: RunState,
        action: RunAction,
        *,
        cause: str,
    ) -> None:
        previous = self._run_state
        self._run_state = run_transition(self._run_state, action)
        if self._run_state is not new_state:
            # Defensive — if the table disagrees with the caller, trust the table.
            new_state = self._run_state
        self._publish_run_state(previous, cause)

    def _publish_run_state(self, previous: RunState, cause: str) -> None:
        # Bump the transition wall-clock here, the single choke point for
        # every state change (both `_set_run_state` and the OBS
        # publish-notification fast paths funnel through here).
        self._run_state_changed_at = self._clock()
        if self._bus is None:
            return
        self._bus.put(
            BusEvent(
                at=self._clock(),
                monotonic_ns=self._monotonic_clock_ns(),
                target_id=None,
                payload=RunStateChangedEvent(
                    new_state=self._run_state,
                    previous_state=previous,
                    cause=cause,
                ),
            )
        )
        self._update_heartbeat()
        self._notify_bus_soon()

    async def _publish_target_snapshot(
        self,
        target_id: str,
        ui_state: TargetUiState,
        snapshots: tuple[WorkerSnapshot, ...],
    ) -> None:
        if self._bus is None:
            return
        self._bus.put(
            BusEvent(
                at=self._clock(),
                monotonic_ns=self._monotonic_clock_ns(),
                target_id=target_id,
                payload=TargetSnapshotEvent(
                    target_id=target_id,
                    ui_state=ui_state,
                    snapshots_by_role=snapshots,
                ),
            )
        )
        self._notify_bus_soon()

    def _notify_bus_soon(self) -> None:
        """Schedule a `bus.notify()` and hold a strong reference.

        Without the strong reference, asyncio's own task tracking is
        weak and the task can be GC'd before completion (post-Phase-5
        review fix M-1 / M2). The done-callback prunes the set so it
        doesn't grow unbounded under steady state.
        """
        bus = self._bus
        if bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(self._spawn_notify_task, bus)

    def _spawn_notify_task(self, bus: EventBus) -> None:
        try:
            task = asyncio.create_task(bus.notify())
        except RuntimeError:
            # Loop is shutting down; final batch is acceptable to lose.
            return
        self._pending_notifies.add(task)
        task.add_done_callback(self._pending_notifies.discard)

    # ------------------------------------------------------------------
    # Internal: heartbeat + drop alerts
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                self._update_heartbeat()
        except asyncio.CancelledError:
            raise

    async def _drop_alert_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(DROP_ALERT_INTERVAL_SECONDS)
                if self._bus is None:
                    continue
                total = self._bus.dropped_total
                if total > self._last_drop_alert_total:
                    self._last_drop_alert_total = total
                    self._bus.put(
                        BusEvent(
                            at=self._clock(),
                            monotonic_ns=self._monotonic_clock_ns(),
                            target_id=None,
                            payload=DropAlertEvent(dropped_total=total),
                        )
                    )
                    self._notify_bus_soon()
        except asyncio.CancelledError:
            raise

    def _update_heartbeat(self) -> None:
        # Atomic int assignment under the GIL.
        self._heartbeat_ns = self._monotonic_clock_ns()


def aggregate_health_for(target: Target) -> object:
    """Convenience re-export — aggregate_target_health(target) without
    callers needing to import from `app.domain.health` directly. Phase 6
    is the natural consumer.
    """
    return aggregate_target_health(target)
