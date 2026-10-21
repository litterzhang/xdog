"""Inert installed provider metadata; business imports happen only on open."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import ManagedObservers
    from .ports import HostPorts


@dataclass(frozen=True)
class EyesProvider:
    name: str = 'eyes'
    scopes: tuple[str, ...] = ('application',)
    tool_names: tuple[str, ...] = ('eyes',)
    requirements: tuple[str, ...] = ('acp',)

    async def open(self, host: HostPorts) -> ManagedObservers:
        from .manager import ManagedObservers
        binding = ManagedObservers(host)
        try:
            await binding.start()
        except BaseException as startup:
            try:
                await binding.aclose()
            except BaseException as cleanup:
                import asyncio
                # Opening may be observed by both start and its cleanup join.
                # Retain the original startup exception exactly once, alongside
                # every independent cleanup failure. Expected cancellation adds
                # no failure, but still propagates if it is the only outcome.
                failures: list[BaseException] = []
                seen: set[int] = set()

                def retain(error: BaseException) -> None:
                    if isinstance(error, asyncio.CancelledError):
                        return
                    if isinstance(error, BaseExceptionGroup):
                        for item in error.exceptions:
                            retain(item)
                    elif id(error) not in seen:
                        seen.add(id(error))
                        failures.append(error)

                retain(startup)
                retain(cleanup)
                if len(failures) == 1:
                    raise failures[0]
                if failures:
                    raise BaseExceptionGroup('Managed Eyes startup and cleanup failed', failures) from None
                raise startup
            raise
        return binding


provider = EyesProvider()
