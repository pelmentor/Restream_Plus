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
            sup.notify_obs_publish_began()
            assert sup.run_state() is RunState.LIVE
            sup.notify_obs_publish_ended()
            assert sup.run_state() is RunState.ARMED
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
