"""Test utilities are separate from production resource owners."""
import asyncio


class FakeClock:
    def __init__(self, *, start: float = 0.0, limit: float = 60.0) -> None:
        self.value = start
        self.limit = limit

    def now(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds
        if self.value > self.limit:
            raise TimeoutError("Observer exceeded the fake-clock deadline")
        await asyncio.sleep(0)
