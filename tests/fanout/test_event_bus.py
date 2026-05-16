"""EventBus tests — bounded, drop-oldest, single-drainer FIFO."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.domain.run_state import RunState
from app.fanout.event_bus import (
    EVENT_BUS_CAPACITY,
    BusEvent,
    EventBus,
    RunStateChangedEvent,
)


def _bus_event(i: int) -> BusEvent:
    return BusEvent(
        at=datetime.now(tz=UTC),
        monotonic_ns=i,
        target_id=None,
        payload=RunStateChangedEvent(
            new_state=RunState.OFFLINE,
            previous_state=RunState.OFFLINE,
            cause=f"event {i}",
        ),
    )


class TestConstructor:
    def test_default_capacity(self) -> None:
        bus = EventBus()
        assert bus.capacity == EVENT_BUS_CAPACITY

    def test_rejects_zero_capacity(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            EventBus(capacity=0)

    def test_rejects_negative_capacity(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            EventBus(capacity=-5)

    def test_starts_with_zero_dropped(self) -> None:
        bus = EventBus(capacity=10)
        assert bus.dropped_total == 0


class TestDropOldest:
    def test_overflow_increments_drop_counter(self) -> None:
        bus = EventBus(capacity=3)
        for i in range(5):
            bus.put(_bus_event(i))
        # 3 buffered, 2 dropped (the oldest two).
        assert bus.dropped_total == 2

    def test_drop_counter_is_monotonic(self) -> None:
        bus = EventBus(capacity=2)
        for i in range(10):
            bus.put(_bus_event(i))
        first = bus.dropped_total
        bus.put(_bus_event(99))
        assert bus.dropped_total == first + 1


class TestDrainer:
    async def test_drains_all_pending(self) -> None:
        bus = EventBus(capacity=10)
        for i in range(5):
            bus.put(_bus_event(i))

        drained: list[BusEvent] = []

        async def drain_some() -> None:
            async for ev in bus.drain():
                drained.append(ev)
                if len(drained) >= 5:
                    return

        # The drainer needs to be notified to wake up.
        async with asyncio.TaskGroup() as tg:
            tg.create_task(drain_some())
            await asyncio.sleep(0)  # let drainer enter wait
            await bus.notify()

        assert len(drained) == 5
        # FIFO order preserved.
        for i, ev in enumerate(drained):
            assert ev.monotonic_ns == i

    async def test_drainer_blocks_until_notified(self) -> None:
        bus = EventBus(capacity=10)
        drained: list[BusEvent] = []

        async def drain_one() -> None:
            async for ev in bus.drain():
                drained.append(ev)
                return

        task = asyncio.create_task(drain_one())
        await asyncio.sleep(0.01)
        assert drained == []  # nothing put yet
        bus.put(_bus_event(0))
        await bus.notify()
        await asyncio.wait_for(task, timeout=1.0)
        assert len(drained) == 1


class TestBatchedDrain:
    async def test_batches_pending_in_one_wake(self) -> None:
        bus = EventBus(capacity=100)
        for i in range(10):
            bus.put(_bus_event(i))

        drained: list[BusEvent] = []

        async def drain_until(n: int) -> None:
            async for ev in bus.drain():
                drained.append(ev)
                if len(drained) >= n:
                    return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(drain_until(10))
            await asyncio.sleep(0)
            await bus.notify()

        assert len(drained) == 10
