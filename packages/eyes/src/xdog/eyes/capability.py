"""Inert tool metadata; opening only ensures the shared Eyes daemon is available."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.eyes.cli_process import CliProcess
    from xdog.eyes.tool import EyesTool


class EyesCapability:
    """A tool binding, not an observation manager or event consumer."""

    def __init__(self, control: CliProcess) -> None:
        from xdog.eyes.tool import EyesTool

        self.control, self.closed = control, False
        self.tools: tuple[EyesTool, ...] = (EyesTool(self),)

    async def aclose(self) -> None:
        self.closed = True
        await self.control.aclose()  # Detach only; the daemon owns all registrations.


@dataclass(frozen=True)
class EyesProvider:
    name: str = 'eyes'
    scopes: tuple[str, ...] = ('application',)
    tool_names: tuple[str, ...] = ('eyes',)
    skill_names: tuple[str, ...] = ('eyes',)
    requirements: tuple[str, ...] = ()

    async def open(self, host: object) -> EyesCapability:
        from xdog.eyes.cli_process import CliProcess

        # Public CLI only; no host-private daemon client or business runtime.
        control = CliProcess()
        try:
            result = await control.run(('daemon', 'start'))
            if result['exit_code'] != 0:
                raise RuntimeError('Eyes daemon CLI startup failed: ' + result['stderr'])
            return EyesCapability(control)
        except BaseException:
            await control.aclose()
            raise


provider = EyesProvider()
