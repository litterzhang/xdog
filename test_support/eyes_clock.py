"""Deterministic clocks for the observation engine, no scheduler runtime."""
import asyncio


class Clock:
    def __init__(self, wall=100., mono=10.):
        self.wall, self.mono, self.sleepers = wall, mono, set()

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.sleepers.add(future)
        try:
            await future
        finally:
            self.sleepers.discard(future)

    def advance(self, seconds, *, wall=None):
        self.mono += seconds
        self.wall = self.wall + seconds if wall is None else wall
        for future in tuple(self.sleepers):
            if not future.done():
                future.set_result(None)


async def until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError('observation engine did not reach expected state')
