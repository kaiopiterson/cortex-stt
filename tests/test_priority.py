import asyncio

import pytest

from app.services.priority import PrioritySlots


@pytest.mark.asyncio
async def test_priority_and_fifo():
    slots = PrioritySlots(1)
    await slots.acquire()
    order = []

    async def job(name, priority):
        await slots.acquire(priority)
        order.append(name)
        slots.release()

    tasks = [
        asyncio.create_task(job(name, priority))
        for name, priority in [("normal", 1), ("first", 0), ("second", 0)]
    ]
    await asyncio.sleep(0)
    slots.release()
    await asyncio.gather(*tasks)
    assert order == ["first", "second", "normal"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_capacity():
    slots = PrioritySlots(1)
    await slots.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(slots.acquire(), 0.01)
    slots.release()
    await asyncio.wait_for(slots.acquire(), 1)
    slots.release()
