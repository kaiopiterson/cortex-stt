import asyncio

import pytest

from app.api.websocket import next_frame


@pytest.mark.asyncio
async def test_receiver_failure_propagates():
    async def fail():
        raise RuntimeError("receiver failed")

    receiver = asyncio.create_task(fail())
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(next_frame(asyncio.Queue(), receiver, 30), timeout=1)


@pytest.mark.asyncio
async def test_finished_receiver_preserves_queued_frame():
    queue = asyncio.Queue()
    await queue.put({"text": "finish"})

    async def done():
        return None

    receiver = asyncio.create_task(done())
    await receiver
    assert await next_frame(queue, receiver, 1) == {"text": "finish"}
