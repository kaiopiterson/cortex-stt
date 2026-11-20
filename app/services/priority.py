"""Cancellation-safe priority admission for native inference workers."""
import asyncio
import heapq
from itertools import count


class PrioritySlots:
    def __init__(self, capacity: int):
        self.available = capacity
        self.waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self.sequence = count()

    async def acquire(self, priority: int = 1) -> None:
        if self.available and not self.waiters:
            self.available -= 1
            return
        waiter = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiters, (priority, next(self.sequence), waiter))
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self.release()
            else:
                waiter.cancel()
                self.waiters = [entry for entry in self.waiters if entry[2] is not waiter]
                heapq.heapify(self.waiters)
            raise

    def release(self) -> None:
        while self.waiters:
            _, _, waiter = heapq.heappop(self.waiters)
            if not waiter.done():
                waiter.set_result(None)
                return
        self.available += 1
