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
import collections
import contextlib
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final, Protocol

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.key_material import KeyMaterial
from app.crypto.aead import decrypt_credential
from app.domain.credential_policy import CredentialLifetime, lifetime_for
from app.domain.errors import IllegalRunStateTransitionError, RunBlockedByRotationError
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
    HostCpuByTarget,
    HostStatsEvent,
    RunStateChangedEvent,
    TargetSnapshotEvent,
)
from app.fanout.ffmpeg_urls import compose_out_url
from app.fanout.ffmpeg_worker import FFmpegWorker
from app.fanout.host_stats import (
    HOST_STATS_TICK_SECONDS,
    HostStatsSampler,
    WorkerPidProvider,
)
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


PENDING_ACTIONS_CAP: Final[int] = 64
"""Slice 3 (Hex Audit BA-F7 / FG-F5 / FG2-C2 / FG2-H6): bound on the
deferred-action deque. nginx-rtmp + MediaMTX should only ever enqueue
a handful of items (each represents a real OBS publish/unpublish
event); a runaway webhook storm that exceeds this cap drops the
oldest entry and emits a single structlog warning. The cap is large
enough that legitimate operation never hits it (a Began/Ended pair
per OBS session is the norm) but small enough that an unbounded
producer cannot use this as a memory-exhaustion vector."""


_WIPE_BATCH_SIZE: Final[int] = 5
"""Hex Audit BA2-F5 / FG3-F5 STRONG (slice 10): per-batch cap on
`_wipe_per_session_credentials` to bound the SQLite write-lock
duration per txn. With 30+ VK targets the pre-slice-10 single-txn
version held the write lock for ~2× target_count statement
turnarounds; per-batch commits release the lock between batches so a
concurrent OBS `on_publish` webhook insert against `sessions` doesn't
wait on the entire wipe. 5 targets per batch ≈ 10 statement
turnarounds — well under `busy_timeout=5000ms` even on a slow disk."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class _PendingActionKind(str, Enum):
    """Slice 3: discriminator for the deferred-action queue. The
    webhook handlers enqueue one of these on every nginx-rtmp /
    MediaMTX signal; the supervisor drains them under `_lock`.

    Only the two OBS-publish lifecycle signals need the deferred-
    action treatment today. Other supervisor inputs (REST start/stop,
    enable_target, disable_target) already run on the request task
    that's awaiting the supervisor coroutine, so they don't suffer
    the sync-webhook race we're closing here.
    """

    OBS_PUBLISH_BEGAN = "obs_publish_began"
    OBS_PUBLISH_ENDED = "obs_publish_ended"


@dataclass(frozen=True, slots=True)
class _PendingAction:
    """One queued OBS-publish signal awaiting drain under `_lock`.

    `at` is the wall-clock arrival time (clock-of-record, same source
    as `_run_state_changed_at`). The supervisor logs the arrival-to-
    drain latency at the warning threshold (currently `>1.0 s`) so a
    pathologically slow drain becomes observable instead of silent.
    """

    kind: _PendingActionKind
    at: datetime


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
        rotation_lock: asyncio.Lock | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic_clock_ns: Callable[[], int] = time.monotonic_ns,
        worker_factory: WorkerFactory | None = None,
        host_stats_sampler_factory: (Callable[[WorkerPidProvider], HostStatsSampler] | None) = None,
        host_stats_tick: float = HOST_STATS_TICK_SECONDS,
        youtube_backup_enabled: bool = False,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._key_material = key_material
        self._process_spawner = process_spawner
        self._credential_registry = credential_registry
        self._ingest_loopback_url = ingest_loopback_url
        # Hex Audit SA-F14 (slice 10): explicit gate on the half-baked
        # YouTube primary+backup-worker feature. Threaded into every
        # `target_dto_to_domain` call below.
        self._youtube_backup_enabled = youtube_backup_enabled
        self._clock = clock
        self._monotonic_clock_ns = monotonic_clock_ns
        self._worker_factory = worker_factory or self._default_worker_factory
        # Slice 3 (Hex Audit FG2-H3 / BA-F7): rotation_lock is the
        # auth-layer asyncio.Lock that rotate-passphrase holds for the
        # duration of the re-wrap (`app/auth/deps.py::AuthState.rotation_lock`).
        # The supervisor injects it so `start_run` can acquire it
        # atomically with the OFFLINE pre-check (closes the TOCTOU
        # between the REST handler's advisory check and the actual
        # credential read).
        #
        # Lock ordering rule: `supervisor._lock` is acquired BEFORE
        # `rotation_lock`, never the reverse. Rotate-passphrase honours
        # this via `acquire_state_lock()` (below) which takes
        # `supervisor._lock` first.
        #
        # Defaulted for test convenience — Supervisor() with no
        # rotation_lock gets a private Lock that nothing else touches.
        # Production wires `auth_state.rotation_lock` from main.py.
        self._rotation_lock = rotation_lock if rotation_lock is not None else asyncio.Lock()
        # Optional — when None, the supervisor skips the host-stats loop
        # entirely (used by tests that don't exercise host metrics).
        # Factory receives `self` so the sampler can enumerate worker
        # pids via the supervisor (which IS the natural `WorkerPidProvider`
        # — it owns `_workers`). The factory pattern avoids the
        # construction-order chicken-and-egg of "sampler needs supervisor,
        # supervisor needs sampler."
        self._host_stats_sampler: HostStatsSampler | None = (
            host_stats_sampler_factory(self) if host_stats_sampler_factory is not None else None
        )
        self._host_stats_tick = host_stats_tick

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
        # NOTE: decrypt failures used to live on `self` so the drain
        # loop in `start_run` could run them outside the load session.
        # Hex Audit footgun-hunter FG2-C5 (2026-05-18) showed that an
        # interrupted `start_run` leaked the list into the next attempt,
        # producing duplicate audit rows under repeated KEK-mismatch
        # restarts. `_load_runnable_targets` now returns the failure
        # list as a local; no cross-call instance state.

        # Tracks whether OBS is currently publishing to the local RTMP
        # ingest, INDEPENDENT of `_run_state`. The two are decoupled on
        # purpose: an operator can start OBS BEFORE clicking START on
        # the panel (a common ordering), in which case the auth webhook
        # fires while we're still OFFLINE. With only `_run_state` to
        # check, we'd silently drop that publish signal — and the next
        # ARMED transition (from clicking START) would have no way to
        # learn that OBS is already up, leaving the run wedged on
        # ARMED's "WAITING FOR OBS" body forever. The flag is the
        # ordering-independent record.
        #
        # Slice 3 (Hex Audit BA-F7 / FG-F5 / FG2-C2 / FG2-H6): the flag
        # is now ONLY written by the drain path under `_lock`. The
        # webhook handlers (`notify_obs_publish_*`) append to
        # `_pending_actions` and never touch state directly. This is
        # the single-owner invariant — no more lock-free races with
        # `start_run` / `stop_run` mutating the same fields.
        self._obs_is_publishing: bool = False
        # Slice 3: deferred OBS-publish signals awaiting drain.
        # Bounded at `PENDING_ACTIONS_CAP`; overflow drops the oldest
        # entry (FIFO) and emits a warning so a runaway webhook source
        # is observable, not silent. The deque + GIL-atomic
        # `append`/`popleft` make webhook-side ops O(1) lock-free; the
        # drain side acquires `_lock` to apply queued actions.
        self._pending_actions: collections.deque[_PendingAction] = collections.deque()
        # Strong-reference set for fire-and-forget drain tasks, mirroring
        # `_pending_notifies` for bus notifications. Without it the
        # `call_soon`-spawned drain coroutine is reachable only via
        # asyncio's weak ref and can be GC'd before completion.
        self._pending_drain_tasks: set[asyncio.Task[None]] = set()

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
                host_stats: asyncio.Task[None] | None = None
                if self._host_stats_sampler is not None:
                    host_stats = tg.create_task(
                        self._host_stats_loop(),
                        name="supervisor-host-stats",
                    )
                self._ready_event.set()
                await self._stop_event.wait()
                heartbeat.cancel()
                drop_alert.cancel()
                if host_stats is not None:
                    host_stats.cancel()
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

        Slice 3 (Hex Audit FG2-H3): rotation_lock is acquired atomically
        with the OFFLINE pre-check, under `supervisor._lock`. This
        closes the TOCTOU between the REST handler's advisory
        `state.rotation_lock.locked()` check and the actual credential
        decrypt: rotate-passphrase either has to wait for our
        `_lock` (advisory check still meaningful but unauthoritative)
        OR it grabbed rotation_lock first (in which case we refuse
        with `RunBlockedByRotationError`).
        """
        async with self._lock:
            if self._run_state is not RunState.OFFLINE:
                raise IllegalRunStateTransitionError(
                    state=self._run_state, action=RunAction.START_REQUESTED
                )
            # Slice 3: authoritative rotation gate. The REST-side check
            # at `app/api/run.py::start_run` is advisory (UX hint); a
            # rotation that landed between that check and now would
            # leave workers holding stale DerivedKeys. By acquiring
            # rotation_lock here, the only way for rotate-passphrase
            # to start during our run is to grab `_lock` first — which
            # is impossible while we hold it.
            if self._rotation_lock.locked():
                raise RunBlockedByRotationError(
                    "passphrase rotation in progress; refuse to start a run"
                )
            await self._rotation_lock.acquire()
            try:
                await self._set_run_state(
                    RunState.STARTING,
                    RunAction.START_REQUESTED,
                    cause="user pressed START",
                )

                try:
                    await self._open_run_session()
                    pairs, decrypt_failures = await self._load_runnable_targets()
                    # Flush any decrypt failure audit rows now that the
                    # _load_runnable_targets session has closed (the audit
                    # repo opens its own session; running it inside would
                    # contend on the SQLite write lock). The list is a
                    # local — FG2-C5: no cross-call leak even on a mid-flow
                    # raise after _load_runnable_targets returns.
                    for failed_id in decrypt_failures:
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
                    # Slice 3: drain queued OBS-publish signals that
                    # arrived during STARTING (e.g., operator started
                    # OBS BEFORE clicking START, or webhooks fired
                    # during the multi-second worker spawn). The drain
                    # is the single source of truth for the ARMED↔LIVE
                    # transition and the `_obs_is_publishing` flag —
                    # there is no longer an "auto-promote after ARMED"
                    # special case based on a lock-free flag read.
                    await self._drain_pending_actions_locked()
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
                    #
                    # Hex Audit BA-F10 (slice 3): the ERROR-path used
                    # to leak `_obs_is_publishing = True` into the next
                    # `start_run` (the flag wasn't cleared on this
                    # path). Now we synthesise an `OBS_PUBLISH_ENDED`
                    # action and drain — the drain clears the flag
                    # under `_lock` exactly the way a real webhook
                    # would, so there's only one writer.
                    with contextlib.suppress(Exception):
                        await self._tear_down_all_workers(grace=WORKER_GRACE)
                    self._enqueue_synthetic_action(
                        _PendingActionKind.OBS_PUBLISH_ENDED
                    )
                    with contextlib.suppress(Exception):
                        await self._drain_pending_actions_locked()
                    with contextlib.suppress(Exception):
                        await self._set_run_state(
                            RunState.ERROR,
                            RunAction.UNRECOVERABLE_FAILED,
                            cause="failure during start_run",
                        )
                    with contextlib.suppress(Exception):
                        await self._close_run_session(reason="error")
                    raise
            finally:
                self._rotation_lock.release()

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
            # Slice 3 (FG2-H6): drain queued OBS signals BEFORE the
            # STOPPING transition. A Began that arrived during LIVE but
            # hasn't drained yet should still register (so the audit
            # log captures the publish-attempt before the stop) — and
            # an Ended from the user closing OBS during the stop should
            # also drain so we don't carry a stale Began over.
            await self._drain_pending_actions_locked()
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
            # Slice 3 (FG2-H6 / BA-F7): synthesise an OBS_PUBLISH_ENDED
            # and drain it. The drain is the single writer of
            # `_obs_is_publishing` — so the explicit `= False` is gone,
            # replaced by the same code path a real `runOnNotReady`
            # webhook would take. This closes the race where a webhook
            # `Began` could land AFTER `_obs_is_publishing = False`
            # but BEFORE the next `start_run`, leaving the flag True
            # for the next attempt.
            #
            # MediaMTX's `runOnNotReady` SHOULD also fire — but we
            # don't depend on that ordering; the synthetic Ended is
            # the authoritative wipe.
            self._enqueue_synthetic_action(_PendingActionKind.OBS_PUBLISH_ENDED)
            with contextlib.suppress(Exception):
                await self._drain_pending_actions_locked()
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
    # nginx-rtmp / MediaMTX webhook signals (Phase 6 wires the HTTP route)
    # ------------------------------------------------------------------

    def notify_obs_publish_began(self) -> None:
        """Record an OBS publish; transitions handled by the drain path.

        Slice 3 (Hex Audit BA-F7 / FG-F5 / FG2-C2 / FG2-H6): this is
        now a non-blocking enqueue. The webhook handler (`on_publish`
        in `app/api/internal_rtmp.py` and `on_auth` in
        `app/api/internal_mtx.py`) returns to nginx-rtmp / MediaMTX
        in microseconds. The supervisor drains queued actions under
        `_lock` at safe checkpoints (top of `stop_run`, after
        `STARTING → ARMED` in `start_run`) and also schedules an
        immediate drain task via `_schedule_drain` so steady-state
        webhook signals are applied within one event-loop tick.

        The previous arrangement mutated `_run_state` and
        `_obs_is_publishing` directly from this method, lock-free,
        from request handlers. That raced with `start_run` and
        `stop_run` which mutate the same fields under `_lock`. The
        queue + drain pattern means there is ONE writer to those
        fields (the drain, under `_lock`); webhook signals are
        observed in FIFO order.

        OBS-started-BEFORE-START ordering is preserved: the action
        sits in the deque while we're OFFLINE; `start_run`'s post-
        ARMED drain consumes it and transitions ARMED → LIVE.
        """
        self._enqueue_action(_PendingActionKind.OBS_PUBLISH_BEGAN)
        self._schedule_drain()

    def notify_obs_publish_ended(self) -> None:
        """Record OBS unpublish; transitions handled by the drain path.

        See `notify_obs_publish_began` — same shape, same single-writer
        invariant. Per ADR-0003 the idle-timeout fallback is upstream
        of this; this signal is for the immediate transition.
        """
        self._enqueue_action(_PendingActionKind.OBS_PUBLISH_ENDED)
        self._schedule_drain()

    # ------------------------------------------------------------------
    # Lifecycle locking (rotate-passphrase ordering helper)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire_state_lock(self) -> AsyncIterator[None]:
        """Acquire `supervisor._lock` for a single brief critical section.

        Slice 3 (Hex Audit FG2-H3): exposed exclusively for
        `rotate-passphrase` (`app/api/security_api.py`) so it can
        honour the supervisor-lock-before-rotation-lock ordering
        invariant. The rotate-passphrase handler takes this lock
        briefly to check `run_state() == OFFLINE` and acquire
        `state.rotation_lock` atomically — once rotation_lock is held,
        the supervisor's own `start_run` will refuse via
        `RunBlockedByRotationError`, so the supervisor-lock can be
        released for the duration of the multi-second re-wrap.

        Do NOT widen the caller set without a design memo. The lock
        protects the entire state-machine + worker-construction
        critical section; holders that don't follow the ordering rule
        (rotation_lock AFTER supervisor._lock, never the reverse) can
        deadlock the run-control path.
        """
        async with self._lock:
            yield

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
    ) -> tuple[list[tuple[TargetDTO, Target, str | None]], list[str]]:
        """Load enabled targets and decrypt their credentials.

        Returns:
            `(pairs, decrypt_failures)`. `pairs` is the per-target tuple
            list; `decrypt_failures` is a list of target ids whose
            credential decrypt raised (the caller writes one audit row
            per id once the load session closes — running the audit
            repo inside the same session would contend on the SQLite
            write lock).

            Per Hex Audit FG2-C5 (2026-05-18): the failure list is a
            local return value (not instance state) so an interrupted
            `start_run` cannot leak entries into the next attempt.
        """
        decrypt_failures: list[str] = []
        derived = self._key_material.require_ready()
        async with self._sessionmaker() as session:
            targets_repo = TargetsRepository(session)
            creds_repo = CredentialsRepository(session)
            enabled = await targets_repo.list_enabled()
            results: list[tuple[TargetDTO, Target, str | None]] = []
            for t_dto in enabled:
                cred = await creds_repo.get_for_target(t_dto.id)
                t_dom = target_dto_to_domain(
                    t_dto, cred, youtube_backup_enabled=self._youtube_backup_enabled
                )
                plaintext: str | None = None
                if cred is not None:
                    try:
                        # Hex Audit BA-F5 (slice 7): rolling-migration
                        # decrypt — tries v2 AAD (binds target_id),
                        # falls back to v1 for legacy rows.
                        plaintext_bytes = decrypt_credential(
                            derived.stream_key_kek,
                            cred.nonce,
                            cred.ciphertext,
                            cred.salt,
                            t_dto.id,
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
                        decrypt_failures.append(t_dto.id)
                        plaintext = None
                    else:
                        plaintext = plaintext_bytes.decode("utf-8", errors="strict")
                results.append((t_dto, t_dom, plaintext))
            return results, decrypt_failures

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
            t_dom = target_dto_to_domain(
                t_dto, cred, youtube_backup_enabled=self._youtube_backup_enabled
            )
            plaintext: str | None = None
            if cred is not None:
                try:
                    # Hex Audit BA-F5 (slice 7): rolling-migration
                    # decrypt — tries v2 AAD, falls back to v1.
                    plaintext_bytes = decrypt_credential(
                        derived.stream_key_kek,
                        cred.nonce,
                        cred.ciphertext,
                        cred.salt,
                        target_id,
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
        # Hex Audit BA-F4 (slice 8): delete + audit in ONE txn so a
        # successful wipe is always paired with a forensic record.
        #
        # Hex Audit BA2-F5 / FG3-F5 STRONG (slice 10): chunk the wipe
        # so the SQLite write-lock duration is bounded per
        # `WIPE_BATCH_SIZE` regardless of operator-configured target
        # count. With 30+ VK targets, the pre-slice-10 single-txn
        # version held the write lock for ~2× target_count statement
        # turnarounds; concurrent OBS `on_publish` webhook inserts
        # against `sessions` could trip `busy_timeout=5000` and drop
        # the publish. Per-chunk commits release the lock between
        # batches so the webhook never waits more than one chunk's
        # worth of writes. Atomicity is preserved per-target (the
        # delete + audit pair within one batch is one txn); cross-
        # target atomicity was never a requirement (each VK session
        # credential is independent of the others).
        audit_repo = AuditLogRepository(self._sessionmaker)
        for batch_start in range(0, len(per_session_targets), _WIPE_BATCH_SIZE):
            batch = per_session_targets[batch_start : batch_start + _WIPE_BATCH_SIZE]
            async with self._sessionmaker() as session, session.begin():
                creds_repo = CredentialsRepository(session)
                for t in batch:
                    if await creds_repo.delete_for_target(t.id):
                        await audit_repo.append(
                            session,
                            event_type="vk_session_credential_cleared",
                            target_id=t.id,
                        )

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
        """Standalone audit emission for supervisor lifecycle events.

        Used by `supervisor_started`, `run_started`, `run_stopped`,
        `worker_failed_open`, `credential_decrypt_failed` — i.e.,
        state-machine events that don't share a caller's business
        transaction.

        Slice 8.5 (Hex Audit BA2-F4): passes `checkpoint=False`. The
        supervisor's hot path can fire `_audit` multiple times per
        second during a flapping run (`worker_failed_open` per
        breaker trip); per-event `wal_checkpoint(TRUNCATE)` against a
        write-locked DB is the exact pattern BA-F4 (slice 8) aimed to
        remove from the canonical path. Checkpoint cadence is owned
        by the retention loop's 6 h / 24 h idle cadence.

        Site-specific audits that DO have a caller session (e.g.,
        `vk_session_credential_cleared` inside the wipe txn) call
        `audit_repo.append(session, ...)` directly — they do NOT route
        through this helper.

        Hex Audit FG3-F10 (slice 10): on `IntegrityError` (the FK race
        where the target was DELETEd between worker-event arrival and
        the audit append's flush), retry with `target_id=None` and
        push the original id into `data` — same shape as the
        `target_deleted` row. The forensic record survives; the FK
        violation does not propagate up the supervisor's hot path.
        """
        repo = AuditLogRepository(self._sessionmaker)
        data_dict = dict(data) if data is not None else None
        try:
            await repo.append_standalone(
                event_type=event_type,
                target_id=target_id,
                data=data_dict,
                checkpoint=False,
            )
        except IntegrityError:
            # Defensive degradation per FG3-F10. Move target_id into the
            # payload so the audit log retains the identifier as a
            # JSON-blob value even though the FK column ends up NULL
            # (post-DELETE the row is gone and the FK reference cannot
            # land).
            fallback_data = dict(data_dict) if data_dict is not None else {}
            if target_id is not None:
                fallback_data.setdefault("target_id", target_id)
                fallback_data.setdefault("fk_race", True)
            try:
                await repo.append_standalone(
                    event_type=event_type,
                    target_id=None,
                    data=fallback_data,
                    checkpoint=False,
                )
            except Exception:
                _logger.exception(
                    "supervisor_audit_fallback_failed",
                    event_type=event_type,
                    original_target_id=target_id,
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
            task = asyncio.create_task(self._notify_with_watchdog(bus))
        except RuntimeError:
            # Loop is shutting down; final batch is acceptable to lose.
            return
        self._pending_notifies.add(task)
        task.add_done_callback(self._pending_notifies.discard)

    async def _notify_with_watchdog(self, bus: EventBus) -> None:
        """Wrap `bus.notify()` in a wall-clock timeout.

        Hex Audit FG2-H4 (slice 10): defence against a wedged
        `_pending_notifies` set growing unbounded if a downstream
        deadlock kept `notify()` from ever returning. The legitimate
        `notify()` is an `async with cond: cond.notify_all()` — sub-
        millisecond. A 5-second timeout is several orders of magnitude
        higher than any plausible legitimate case AND short enough that
        a stuck task does not sit in `_pending_notifies` for the
        process lifetime.
        """
        try:
            await asyncio.wait_for(bus.notify(), timeout=5.0)
        except TimeoutError:
            _logger.warning("supervisor_notify_watchdog_fired", timeout_s=5.0)

    # ------------------------------------------------------------------
    # Internal: pending-action queue drain (Slice 3 — Hex Audit BA-F7 /
    # FG-F5 / FG2-C2 / FG2-H6)
    #
    # `notify_obs_publish_*` enqueues `_PendingAction` items here; the
    # drain runs under `_lock` so all state-machine writes are
    # serialised through one owner. The webhook side never blocks on
    # the lock — `_enqueue_action` is `deque.append + len()`, both
    # O(1) and GIL-atomic; `_schedule_drain` is `loop.call_soon`.
    # ------------------------------------------------------------------

    def _enqueue_action(self, kind: _PendingActionKind) -> None:
        """Append a webhook-arrived OBS-publish signal.

        Bounded at `PENDING_ACTIONS_CAP`; an overflow drops the oldest
        entry (preserving most-recent signal) and emits one warning
        per drop so a runaway producer is observable.
        """
        if len(self._pending_actions) >= PENDING_ACTIONS_CAP:
            dropped = self._pending_actions.popleft()
            _logger.warning(
                "pending_action_dropped_at_cap",
                dropped_kind=dropped.kind.value,
                cap=PENDING_ACTIONS_CAP,
            )
        self._pending_actions.append(_PendingAction(kind=kind, at=self._clock()))

    def _enqueue_synthetic_action(self, kind: _PendingActionKind) -> None:
        """Same as `_enqueue_action` but tagged for internally-generated
        signals (stop_run cleanup, start_run ERROR-path).

        Today both shapes share the queue identically; the separation
        exists to make `grep`-able tracing easy: every synthetic
        injection has a single call site, so a future bug ("why is
        the queue showing two Ended signals after stop_run?") can be
        traced back to the originating internal path without
        confusing it with a real MediaMTX `runOnNotReady`. Cap
        behavior is the same.
        """
        if len(self._pending_actions) >= PENDING_ACTIONS_CAP:
            dropped = self._pending_actions.popleft()
            _logger.warning(
                "pending_action_dropped_at_cap_synthetic",
                dropped_kind=dropped.kind.value,
                cap=PENDING_ACTIONS_CAP,
            )
        self._pending_actions.append(_PendingAction(kind=kind, at=self._clock()))

    def _schedule_drain(self) -> None:
        """Schedule an out-of-line drain task; mirrors `_notify_bus_soon`.

        Webhook callers MUST stay synchronous and non-blocking — the
        `call_soon` here ensures the actual `_drain_pending_actions`
        runs on the event loop ASAP without making the webhook itself
        await `_lock`. If no loop is running (test setup with a bare
        Supervisor and no `tg.create_task(sup.run(bus))`), the drain
        is skipped — tests in that shape call `_drain_pending_actions`
        directly to assert state.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(self._spawn_drain_task)

    def _spawn_drain_task(self) -> None:
        try:
            task = asyncio.create_task(
                self._drain_pending_actions(), name="supervisor-drain"
            )
        except RuntimeError:
            # Loop shutting down — anything still queued at process
            # exit is acceptable to lose (a clean stop_run already
            # drained the last meaningful state).
            return
        self._pending_drain_tasks.add(task)
        # Order matters: log first, then discard. CPython today
        # holds the task on its call stack while iterating
        # done-callbacks so registration order doesn't risk GC,
        # but matching the `app/api/run.py::_spawn_supervisor_task`
        # precedent keeps the pattern uniform and immune to any
        # future change in callback dispatch semantics.
        task.add_done_callback(self._log_drain_task_exception)
        task.add_done_callback(self._pending_drain_tasks.discard)

    @staticmethod
    def _log_drain_task_exception(task: asyncio.Task[Any]) -> None:
        """Surface drain-task crashes via structlog, same pattern as
        `app/api/run.py::_log_supervisor_task_exception`. Without this
        a coroutine exception inside the drain would only show up as
        an asyncio `task exception was never retrieved` warning at
        process exit — invisible to operators in normal logging.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        _logger.error(
            "supervisor_drain_failed",
            task_name=task.get_name(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    async def _drain_pending_actions(self) -> None:
        """Acquire `_lock` and apply all queued actions.

        Public-ish entry point used by `_schedule_drain`'s call_soon-
        spawned task. Tests call it directly to deterministically
        drain after a `notify_obs_publish_*` call without depending
        on event-loop scheduling.
        """
        async with self._lock:
            await self._drain_pending_actions_locked()

    async def _drain_pending_actions_locked(self) -> None:
        """Apply queued actions; caller MUST hold `_lock`.

        FIFO order. Each action updates `_obs_is_publishing` (single
        writer; the only place this field is written) and then asks
        the state machine to transition iff legal. Illegal transitions
        (e.g., a Began while STARTING) are intentional no-ops on the
        state machine — the flag still updates, which is what the
        next `_drain_pending_actions_locked` from `start_run` reads.

        Re-entrancy safety: `_set_run_state` IS an await point, so
        the loop body suspends. The safety property is FIFO-preserved
        across suspends: webhook handlers only ever `append` to the
        tail via `_enqueue_action`, so a new action arriving
        mid-suspend is drained on the NEXT loop iteration (or the
        next drain) — never reordered ahead of the popped action.
        The deque is the ordering primitive; the lock guarantees
        single-writer semantics on `_run_state` and
        `_obs_is_publishing`.
        """
        while self._pending_actions:
            action = self._pending_actions.popleft()
            if action.kind is _PendingActionKind.OBS_PUBLISH_BEGAN:
                self._obs_is_publishing = True
                if self._run_state is RunState.ARMED:
                    with contextlib.suppress(IllegalRunStateTransitionError):
                        await self._set_run_state(
                            RunState.LIVE,
                            RunAction.OBS_PUBLISH_BEGAN,
                            cause="OBS started publishing",
                        )
            else:  # OBS_PUBLISH_ENDED
                self._obs_is_publishing = False
                if self._run_state is RunState.LIVE:
                    with contextlib.suppress(IllegalRunStateTransitionError):
                        await self._set_run_state(
                            RunState.ARMED,
                            RunAction.OBS_PUBLISH_ENDED,
                            cause="OBS stopped publishing",
                        )

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

    async def _host_stats_loop(self) -> None:
        """Periodic CPU% + ingest sample → HostStatsEvent → bus.

        Skips publishing if the sampler raises (already logged inside).
        A persistent sampler failure does NOT crash the supervisor; the
        feature degrades silently and the UI renders "—" for missing
        values.
        """
        sampler = self._host_stats_sampler
        if sampler is None:
            return
        try:
            while True:
                await asyncio.sleep(self._host_stats_tick)
                if self._bus is None:
                    continue
                try:
                    sample = await sampler.sample()
                except Exception:
                    _logger.warning("host_stats_sample_failed", exc_info=True)
                    continue
                self._bus.put(
                    BusEvent(
                        at=self._clock(),
                        monotonic_ns=self._monotonic_clock_ns(),
                        target_id=None,
                        payload=HostStatsEvent(
                            cpu_total_pct=sample.cpu_total_pct,
                            cpu_by_target=tuple(
                                HostCpuByTarget(worker_id=wid, cpu_pct=pct)
                                for wid, pct in sample.cpu_by_target
                            ),
                            rss_bytes=sample.rss_bytes,
                            ingest_kbps=sample.ingest_kbps,
                        ),
                    )
                )
                self._notify_bus_soon()
        except asyncio.CancelledError:
            raise

    def worker_pids(self) -> tuple[tuple[WorkerId, int], ...]:
        """Enumerate live `(worker_id, pid)` pairs for the host-stats
        sampler. Implements the `WorkerPidProvider` Protocol — supervisor
        is the natural owner of this view because it owns `_workers`.

        Workers without a live process (idle, between reconnects) are
        skipped — `_read_and_account` would just record 0% for them
        anyway, and dropping them keeps the per-target list tight.
        """
        out: list[tuple[WorkerId, int]] = []
        for worker_id, worker in self._workers.items():
            pid = worker.pid
            if pid is None:
                continue
            out.append((worker_id, pid))
        return tuple(out)

    def _update_heartbeat(self) -> None:
        # Atomic int assignment under the GIL.
        self._heartbeat_ns = self._monotonic_clock_ns()


def aggregate_health_for(target: Target) -> object:
    """Convenience re-export — aggregate_target_health(target) without
    callers needing to import from `app.domain.health` directly. Phase 6
    is the natural consumer.
    """
    return aggregate_target_health(target)
