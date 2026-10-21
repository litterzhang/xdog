"""Pinned in-process command callbacks, contributed by trusted installed owners.

This is the native side of ExtensionRuntime's existing command catalog, not a
loader, worker, tool registry or authority-bearing wire protocol.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from xdog.agent.async_work import wait_owned
from xdog.agent.cross_loop import submit_remote
from xdog.agent.extensions.api import identifier
from xdog.agent.extensions.commands import public_name

Callback = Callable[[str], Awaitable[str]]
Commands = Mapping[str, tuple[str, Callback]]


class StaleCommandOwner(ValueError):
    """A retired callback cannot publish output; not a shutdown cleanup failure."""


@dataclass(frozen=True)
class Binding:
    extension: str
    commands: Mapping[str, tuple[str, Callback]]
    valid: Callable[[], bool]
    shadow_skills: frozenset[str]

    def rows(self) -> list[dict[str, str]]:
        return [{'name': name, 'public_name': name, 'description': value[0]}
                for name, value in self.commands.items()]


def prepare(extension: str, commands: Commands, valid: Callable[[], bool],
            shadow_skills: set[str] | frozenset[str]) -> Binding:
    extension = identifier(extension)
    if not callable(valid) or not isinstance(commands, Mapping) or not 1 <= len(commands) <= 32:
        raise ValueError('Native commands require bounded declarations and an actual owner validity callback')
    copied: dict[str, tuple[str, Callback]] = {}
    for name, value in commands.items():
        public_name(name)
        if (not isinstance(value, tuple) or len(value) != 2 or not isinstance(value[0], str)
                or len(value[0].encode()) > 1000 or not callable(value[1])):
            raise ValueError('Native commands require descriptions and actual async callbacks')
        copied[name] = value
    if (not isinstance(shadow_skills, (set, frozenset))
            or any(public_name(name) not in copied for name in shadow_skills)):
        raise ValueError('Skill shadow declarations must name this binding\'s actual public commands')
    return Binding(extension, MappingProxyType(copied), valid, frozenset(shadow_skills))


class NativeCommands:
    def __init__(self) -> None:
        self._bindings: dict[str, Binding] = {}
        self._tasks: set[asyncio.Task[str]] = set()
        self._known: set[str] = set()
        self._lock = threading.RLock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def active(self) -> tuple[Binding, ...]:
        with self._lock:
            return tuple(binding for binding in self._bindings.values()
                         if not self._closed and binding.valid() is True)

    def current(self, binding: Binding) -> bool:
        with self._lock:
            return (not self._closed and self._bindings.get(binding.extension) is binding
                    and binding.valid() is True)

    def bind(self, binding: Binding) -> None:
        with self._lock:
            previous = next((value for name, value in self._bindings.items()
                             if name.lower() == binding.extension.lower()), None)
            if self._closed or binding.valid() is not True or previous is not None and self.current(previous):
                raise ValueError('Native command owner is closed, stale or already bound')
            if previous is not None:
                self._bindings.pop(previous.extension)
            self._bindings[binding.extension] = binding
            self._known.update(name for name in binding.commands)
            self._known.update(f'ext-{binding.extension}-{name}'.lower() for name in binding.commands)

    def remove(self, binding: Binding) -> bool:
        with self._lock:
            if self._bindings.get(binding.extension) is not binding:
                return False
            self._bindings.pop(binding.extension)
            return True

    def resolve(self, extension: str, name: str) -> Binding | None:
        with self._lock:
            binding = self._bindings.get(extension)
            return binding if binding is not None and name in binding.commands and self.current(binding) else None

    def claims(self, command: str) -> bool:
        with self._lock:
            return command.lower() in self._known

    async def command(self, binding: Binding, name: str, args: str,
                      routed: Callable[[], bool]) -> str:
        if not isinstance(args, str) or '\x00' in args or len(args.encode()) > 1024 * 1024:
            raise ValueError('Native command arguments require bounded text')

        async def run() -> str:
            if not self.current(binding) or not routed():
                raise StaleCommandOwner('Native command owner is stale')
            result = await binding.commands[name][1](args)
            if not self.current(binding) or not routed():
                raise StaleCommandOwner('Native command owner is stale; late output was not published')
            if not isinstance(result, str) or len(result.encode()) > 1024 * 1024:
                raise ValueError('Native command callback must return bounded text')
            return result

        with self._lock:
            if not self.current(binding):
                raise StaleCommandOwner('Native command owner is stale')
            task = asyncio.create_task(run(), name='native-command:' + binding.extension + ':' + name)
            self._tasks.add(task)
        try:
            # Caller cancellation requests cancellation once, then joins the real
            # callback cleanup before returning. No detached successful output.
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelling():
                    task.cancel()
                try:
                    await wait_owned(task)
                except (Exception, asyncio.CancelledError):
                    pass
                raise
        finally:
            with self._lock:
                if not self._closed:
                    self._tasks.discard(task)
                # Once close owns cancellation, retain its completed tasks until
                # aclose has retrieved their actual cleanup outcomes as well.

    def close(self) -> None:
        """Retire admission synchronously; aclose is the actual settlement join."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._bindings.clear()
            tasks = tuple(self._tasks)

        def cancel(task: asyncio.Task[str]) -> None:
            if not task.done() and not task.cancelling():
                task.cancel()

        for task in tasks:
            if not task.done():
                task.get_loop().call_soon_threadsafe(cancel, task)

    async def _close(self) -> None:
        with self._lock:
            tasks = tuple(self._tasks)

        async def join(task: asyncio.Task[str]) -> None:
            if not task.done():
                if task.get_loop() is not asyncio.get_running_loop():
                    await submit_remote(task.get_loop(), join(task))
                    return
            try:
                await wait_owned(task)
            except (asyncio.CancelledError, StaleCommandOwner):
                pass  # Expected shutdown cancellation, after actual callback cleanup.

        results = await asyncio.gather(*(join(task) for task in tasks), return_exceptions=True)
        with self._lock:
            self._tasks.difference_update(tasks)
        errors = [result for result in results if isinstance(result, BaseException)]
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup('Native command cleanup failed', errors)

    async def aclose(self) -> None:
        self.close()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name='native-command-close')
        await wait_owned(self._close_task)
