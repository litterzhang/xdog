"""Test-only adaptation for lifecycle regressions intentionally configured to one input per turn."""
from collections.abc import Awaitable, Callable

from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.batches import TurnBatch
from xdog.coding.core.sessions.models import TurnResult


def single_input_execute(operation: Callable[[SessionInput], Awaitable[TurnResult]]) -> Callable[
    [TurnBatch], Awaitable[TurnResult]
]:
    async def execute(batch: TurnBatch) -> TurnResult:
        assert len(batch.items) == 1, 'legacy single-input regression must explicitly configure a limit of one'
        return await operation(batch.items[0].value)
    return execute
