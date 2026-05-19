"""Supervisor end-to-end tests via FakeProcessSpawner.

Covers the public surface — start_run / stop_run / enable_target /
disable_target / notify_obs_publish_began / heartbeat — and the
private invariants (credential decrypt + register, VK per-session
credential wipe on stop_run, run-session row open + close).

Real subprocesses are never spawned. The Supervisor's
`worker_factory` is overridden to inject a custom FFmpegWorker with
zero backoff and tight stall timing.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from datetime import timedelta

import pytest
from app.auth.key_material import KeyMaterial
from app.crypto.aead import build_credential_aad, encrypt
from app.domain.run_state import RunState
from app.domain.target_types import TargetType
from app.fanout.event_bus import (
    BusEvent,
    EventBus,
)
from app.fanout.ffmpeg_worker import FFmpegWorker
from app.fanout.supervisor import Supervisor
from app.fanout.worker import WorkerSpec
from app.logging_setup import get_credential_registry
from app.repositories.credentials import CredentialsRepository
from app.repositories.targets import TargetsRepository
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fanout._fakes import FakeProcess, FakeProcessSpawner

_LOOPBACK_URL = "rtmp://127.0.0.1:1935/live/ingest_test"


@pytest.fixture(autouse=True)
def _zero_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.fanout.ffmpeg_worker.compute_backoff",
        lambda *_a, **_kw: timedelta(0),
    )


def _worker_factory_for_tests(
    spawner: FakeProcessSpawner,
) -> Callable[..., FFmpegWorker]:
    def factory(*, spec: WorkerSpec) -> FFmpegWorker:
        return FFmpegWorker(
            spec=spec,
            process_spawner=spawner,
            stall_timeout=timedelta(seconds=10),
            stall_check_interval=0.05,
            rng=random.Random(0),
        )

    return factory


async def _seed_target(
    sf: async_sessionmaker[AsyncSession],
    *,
    type_: TargetType = TargetType.TWITCH,
    label: str = "Twitch",
    url: str = "rtmp://live.twitch.tv/app",
    enabled: bool = True,
    plaintext_key: str = "twitch_secret_key_12345",
    kek: bytes,
    backup_enabled: bool = False,
) -> str:
    settings = {"backup_enabled": backup_enabled} if backup_enabled else {}
    async with sf() as session, session.begin():
        targets = TargetsRepository(session)
        creds = CredentialsRepository(session)
        t = await targets.create(
            type=type_.value,
            label=label,
            url=url,
            enabled=enabled,
            settings=settings,
        )
        nonce, ct = encrypt(kek, plaintext_key.encode("utf-8"), build_credential_aad(b"\x01" * 16))
        await creds.upsert(
            target_id=t.id,
            lifetime="persistent" if type_ is not TargetType.VK_LIVE else "per_session",
            ciphertext=ct,
            nonce=nonce,
            salt=b"\x01" * 16,
            last4=plaintext_key[-4:],
        )
    return t.id


class TestRunLifecycle:
    async def test_start_run_spawns_workers_and_armed(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await sup.start_run()
            assert sup.run_state() is RunState.ARMED
            await sup.stop_run(reason="user_stop")
            assert sup.run_state() is RunState.OFFLINE
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_obs_publish_began_transitions_to_live(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await sup.start_run()
            # Slice 3: notify_obs_publish_* enqueues; drain happens
            # under _lock on the call_soon-scheduled task. We drain
            # explicitly here to keep the assertion deterministic
            # rather than relying on event-loop tick ordering.
            sup.notify_obs_publish_began()
            await sup._drain_pending_actions()
            assert sup.run_state() is RunState.LIVE
            sup.notify_obs_publish_ended()
            await sup._drain_pending_actions()
            assert sup.run_state() is RunState.ARMED
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_obs_publish_began_before_start_promotes_to_live(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        # Reverse ordering: webhook arrives while we're OFFLINE (operator
        # started OBS BEFORE clicking START on the panel). Slice 3: the
        # action sits in the pending-action deque while we're OFFLINE;
        # start_run's post-ARMED drain consumes it and transitions
        # ARMED → LIVE so the dashboard reflects reality instead of
        # hanging on "WAITING FOR OBS".
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            # OBS publishes BEFORE the operator presses START on the panel.
            sup.notify_obs_publish_began()
            # State is still OFFLINE — the deque holds the action; the
            # state machine is not advanced by enqueue-side ops.
            assert sup.run_state() is RunState.OFFLINE
            await sup.start_run()
            # start_run's post-ARMED drain consumed the queued action,
            # auto-promoting ARMED → LIVE.
            assert sup.run_state() is RunState.LIVE, (
                "start_run drained the pending OBS_PUBLISH_BEGAN and " "auto-promoted past ARMED"
            )
            await sup.stop_run(reason="user_stop")
            assert sup.run_state() is RunState.OFFLINE
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_obs_publish_ended_before_stop_clears_flag(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        # If OBS unpublishes while we're OFFLINE (e.g. the lifecycle
        # webhook arrives between two runs), the flag must clear so the
        # NEXT start_run doesn't false-promote to LIVE on a stale signal.
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        # Hex Audit CR-F8 (slice 10): the test exercises start_run
        # → ARMED → stop_run. start_run spawns one ffmpeg child;
        # depending on event-loop scheduling the lifecycle loop may
        # respawn once before stop_requested is observed (the
        # original code-reviewer comment "consumes 1" turned out to
        # be wrong — empirical run shows up to 2 spawns happen
        # before the stop unwind completes). Pool size restored to
        # 2 with this comment as the authoritative explanation.
        spawner = FakeProcessSpawner([FakeProcess(), FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            # Slice 3: both signals queue. start_run's drain consumes
            # them FIFO: Began → flag True (still OFFLINE, transition
            # illegal, no state change); Ended → flag False. Net
            # result: start_run reaches ARMED and the drain leaves
            # state at ARMED (no promotion to LIVE).
            sup.notify_obs_publish_began()
            sup.notify_obs_publish_ended()
            await sup.start_run()
            assert sup.run_state() is RunState.ARMED, (
                "queue drain processed Began-then-Ended; flag is "
                "False at the post-ARMED drain checkpoint, no "
                "auto-promotion to LIVE"
            )
            await sup.stop_run(reason="user_stop")
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task


class TestVKCredentialWipe:
    async def test_per_session_credential_deleted_on_stop(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        kek = key_material_ready.require_ready().stream_key_kek
        target_id = await _seed_target(
            sessionmaker_factory,
            type_=TargetType.VK_LIVE,
            label="VK Live",
            url="rtmp://ovsu.okcdn.ru/input/",
            plaintext_key="vk_per_session_key",
            kek=kek,
        )

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await sup.start_run()
            await sup.stop_run(reason="user_stop")
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

        # After stop, the credential row must be gone.
        async with sessionmaker_factory() as session:
            cred = await CredentialsRepository(session).get_for_target(target_id)
        assert cred is None


class TestHeartbeat:
    async def test_updates_under_run(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await asyncio.sleep(1.2)
            age_after = sup.heartbeat_age()
            # Heartbeat tick (1 Hz) should refresh the age.
            assert age_after < timedelta(seconds=2.0)
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_age_starts_small(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
    ) -> None:
        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        # Without run(), heartbeat is initialized at construction.
        assert sup.heartbeat_age() < timedelta(seconds=1)


class TestPublishesEvents:
    async def test_run_state_change_reaches_bus(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()
        events: list[BusEvent] = []

        async def _drain() -> None:
            async for ev in bus.drain():
                events.append(ev)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(_drain())
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await sup.start_run()
            await asyncio.sleep(0.1)
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task
            # Drainer is still running; cancel it.
            for t in tg._tasks:
                t.cancel()
        # ExceptionGroup with CancelledError is expected when we cancel
        # the drainer; we caught it implicitly via TaskGroup body.

        kinds = [type(e.payload).__name__ for e in events]
        assert "RunStateChangedEvent" in kinds, kinds


class TestRunStateChangedAt:
    """Phase 8 §A: supervisor tracks the wall-clock of the last transition."""

    async def test_starts_none_and_bumps_on_first_transition(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess()])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        # Before run(): never transitioned.
        assert sup.run_state_changed_at() is None

        bus = EventBus()

        async def _drain() -> None:
            async for _ in bus.drain():
                pass

        async with asyncio.TaskGroup() as tg:
            tg.create_task(_drain())
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)

            await sup.start_run()
            after_start = sup.run_state_changed_at()
            assert after_start is not None, "should bump on STARTING"

            await asyncio.sleep(0.05)
            # ARMED transition happens inside start_run; the timestamp
            # for the latest (ARMED) transition should be >= the
            # earlier (STARTING) bump.
            armed_at = sup.run_state_changed_at()
            assert armed_at is not None
            assert armed_at >= after_start

            await sup.stop(grace=timedelta(seconds=1))
            await sup_task
            for t in tg._tasks:
                t.cancel()

        # Final OFFLINE-after-stop transition further bumped the clock.
        final_at = sup.run_state_changed_at()
        assert final_at is not None
        assert final_at >= armed_at


# ----------------------------------------------------------------------
# Slice 3 (Hex Audit BA-F7 / FG-F5 / FG2-C2 / FG2-H6 / FG2-H3 / BA-F10)
# Supervisor sync-webhook race fixes — queued action pattern + rotation
# lock + ERROR-path flag clear.
# ----------------------------------------------------------------------


class TestSlice3SupervisorRace:
    async def test_start_run_refuses_while_rotation_lock_held(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        """FG2-H3: rotation_lock is the authoritative gate against
        starting a run during passphrase rotation. start_run grabs the
        lock atomically with the OFFLINE pre-check (under
        supervisor._lock) so a rotation can never sneak in between
        check and credential read."""
        from app.domain.errors import RunBlockedByRotationError

        rotation_lock = asyncio.Lock()
        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            rotation_lock=rotation_lock,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            # Simulate rotate-passphrase having already acquired the
            # rotation_lock (it would have done so under
            # supervisor.acquire_state_lock).
            await rotation_lock.acquire()
            try:
                with pytest.raises(RunBlockedByRotationError):
                    await sup.start_run()
                # State must remain OFFLINE — no partial transition.
                assert sup.run_state() is RunState.OFFLINE
            finally:
                rotation_lock.release()
            # After the rotation_lock is released, start_run works
            # again (we exit cleanly through stop()).
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_acquire_state_lock_blocks_start_run(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        """FG2-H3: rotate-passphrase uses acquire_state_lock() to hold
        supervisor._lock while it checks run_state + acquires
        rotation_lock. While we hold acquire_state_lock, start_run
        cannot enter its critical section — it blocks on _lock — so a
        concurrent start_run is impossible during the ordering
        handshake."""
        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            # Hold the state-lock; concurrent start_run must block.
            async with sup.acquire_state_lock():
                start_task = asyncio.create_task(sup.start_run())
                # Give the event loop a chance to schedule it.
                await asyncio.sleep(0.05)
                assert not start_task.done(), (
                    "start_run must wait on supervisor._lock while " "acquire_state_lock holds it"
                )
            # Lock released — start_run now proceeds.
            # We may not have a seeded target so start_run completes
            # quickly through ARMED with zero workers.
            await start_task
            assert sup.run_state() is RunState.ARMED
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task

    async def test_pending_actions_cap_drops_oldest(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        """Cap + FIFO drop is the safety net against a runaway webhook
        producer (mis-configured nginx-rtmp / MediaMTX hook). The
        constant is `PENDING_ACTIONS_CAP`; we exceed it and verify
        the deque does not grow past it."""
        from app.fanout.supervisor import PENDING_ACTIONS_CAP

        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        # No supervisor.run() needed — enqueue is sync.
        # Fire 3× the cap; deque must stay bounded.
        for _ in range(PENDING_ACTIONS_CAP * 3):
            sup.notify_obs_publish_began()
        assert len(sup._pending_actions) == PENDING_ACTIONS_CAP

    async def test_error_path_clears_obs_flag_via_synthetic_ended(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        """BA-F10: a failed start_run must not leak _obs_is_publishing
        = True into the next attempt. The synthetic OBS_PUBLISH_ENDED
        injection inside the except block drains the flag through the
        single-writer drain path.

        Approach: directly exercise the drain path without spawning a
        worker — drive `_pending_actions` + `_drain_pending_actions`
        manually so the test isolates the synthetic-Ended invariant
        without the worker-lifecycle scaffolding.
        """
        from app.fanout.supervisor import _PendingActionKind

        spawner = FakeProcessSpawner([])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        # Simulate the post-spawn state where _obs_is_publishing
        # has been set True by a real Began drain (i.e., we're "LIVE
        # at the moment the spawn failure occurs"). The start_run
        # except path enqueues a synthetic Ended and drains it under
        # _lock, which is exactly what we exercise here.
        sup._obs_is_publishing = True
        sup._enqueue_synthetic_action(_PendingActionKind.OBS_PUBLISH_ENDED)
        # No run() / TaskGroup needed — drain only touches `_lock`
        # and `_pending_actions` (and run_state machinery which is
        # already initialised in __init__).
        await sup._drain_pending_actions()
        assert sup._obs_is_publishing is False, (
            "synthetic OBS_PUBLISH_ENDED through the drain path "
            "did not clear the single-writer flag"
        )

    async def test_stop_run_injects_synthetic_ended(
        self,
        sessionmaker_factory: async_sessionmaker[AsyncSession],
        key_material_ready: KeyMaterial,
        fresh_credential_registry: None,
    ) -> None:
        """FG2-H6: stop_run's bottom drain consumes any queued Began
        and the synthetic Ended wipes the flag. No matter what arrives
        during stop_run, _obs_is_publishing ends up False.

        Uses `exit_on_signal=True` so worker.stop completes quickly
        and the test finishes in <1 second.
        """
        kek = key_material_ready.require_ready().stream_key_kek
        await _seed_target(sessionmaker_factory, kek=kek)

        spawner = FakeProcessSpawner([FakeProcess(exit_on_signal=True)])
        sup = Supervisor(
            sessionmaker=sessionmaker_factory,
            key_material=key_material_ready,
            process_spawner=spawner,
            credential_registry=get_credential_registry(),
            ingest_loopback_url=_LOOPBACK_URL,
            worker_factory=_worker_factory_for_tests(spawner),
        )
        bus = EventBus()

        async with asyncio.TaskGroup() as tg:
            sup_task = tg.create_task(sup.run(bus))
            await sup.wait_ready(timeout=2.0)
            await sup.start_run()
            sup.notify_obs_publish_began()
            await sup._drain_pending_actions()
            assert sup.run_state() is RunState.LIVE
            assert sup._obs_is_publishing is True

            # Now stop_run; the synthetic Ended must clear the flag.
            await sup.stop_run(reason="user_stop")
            assert sup.run_state() is RunState.OFFLINE
            assert sup._obs_is_publishing is False
            await sup.stop(grace=timedelta(seconds=1))
            await sup_task
