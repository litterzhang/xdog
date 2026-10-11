"""Original-child-loop queue proposals and boolean inspection, never parent queue authority."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.agent.agent import Agent
from xdog.agent.events import AgentEndEvent, AgentEvent, InputConsumedEvent
from xdog.agent.extensions.queued_input import Delivery, reviewed
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.supervision import settle_on
from xdog.ai.types import ImageContent

if TYPE_CHECKING:
    from xdog.agent.supervision_extensions import ChildExtensions

T = TypeVar('T')
Admit = Callable[[str, str, Delivery, tuple[ImageContent, ...], Callable[[], bool]], Awaitable[Any]]
Discard = Callable[[str], Awaitable[bool]]


@dataclass
class _Bound:
    agent: Agent
    runtime: ExtensionRuntime
    loop: asyncio.AbstractEventLoop
    validate_images: Callable[[tuple[ImageContent, ...]], Awaitable[None]] | None
    queue_alive: Callable[[], bool] | None
    admit: Admit | None
    discard: Discard | None
    ids: dict[str, dict[str, Any]] = field(default_factory=dict)
    unsubscribe: Callable[[], None] = lambda: None

    async def call(self, work: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if asyncio.get_running_loop() is self.loop:
            return await work()
        if self.loop.is_closed() or not self.loop.is_running():
            raise ValueError('Original child loop is unavailable')
        return await settle_on(self.loop, work())


class ChildQueuedInputs:
    def __init__(self, owner: ChildExtensions) -> None:
        self.owner = owner
        self.tokens: set[str] = set()
        self.generations: dict[str, int] = {}
        self.senders: dict[str, Callable[[dict[str, Any]], Awaitable[bool | None]]] = {}
        self.bound: dict[str, _Bound] = {}
        self.handlers: dict[str, Callable[[str, dict[str, Any]], Awaitable[Any]]] = {}

    def generation(self, token: str) -> int:
        with self.owner._ui_lock:
            return self.generations.get(token, 0)

    def enable(self, token: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError('Child queued input requires an explicit boolean grant')
        with self.owner._ui_lock:
            if enabled != (token in self.tokens):
                self.generations[token] = self.generations.get(token, 0) + 1
            if enabled:
                self.tokens.add(token)
            else:
                self.tokens.discard(token)
            for child, binding in self.owner._bindings.items():
                if binding[0] != token:
                    continue
                runtime = self.owner.owners.get(child)
                if runtime is not None:
                    runtime.queued_input_handler = self.handlers.get(child) if enabled else None
                broker = self.owner._dialogs.get(child)
                if not enabled and broker is not None:
                    broker.cancel_where(lambda dialog: dialog.get('operation') == 'queue_input')

    def set_sender(self, token: str, sender: Callable[[dict[str, Any]], Awaitable[bool | None]] | None) -> None:
        with self.owner._ui_lock:
            if self.senders.get(token) is sender:
                return
            self.generations[token] = self.generations.get(token, 0) + 1
            if sender is None:
                self.senders.pop(token, None)
            else:
                self.senders[token] = sender

    def _attached(self, child: str, bound: _Bound, binding: tuple[str, Callable[[dict[str, Any]], None]]) -> bool:
        return (self.bound.get(child) is bound and self.owner.owners.get(child) is bound.runtime
            and self.owner._bindings.get(child) == binding and not bound.runtime._closed
            and not self.owner.parent._closed)

    def bind(self, child: str, agent: Agent, *,
             validate_images: Callable[[tuple[ImageContent, ...]], Awaitable[None]] | None = None,
             queue_alive: Callable[[], bool] | None = None,
             admit: Admit | None = None, discard: Discard | None = None) -> None:
        runtime = self.owner.owners.get(child)
        if runtime is None or child in self.bound:
            raise ValueError('Unknown or already bound child queued-input owner')
        bound = _Bound(agent, runtime, asyncio.get_running_loop(), validate_images, queue_alive, admit, discard)
        self.bound[child] = bound
        session_id = str(runtime.context().get('session_id', ''))

        def context_alive() -> bool:
            return (self.bound.get(child) is bound and self.owner.owners.get(child) is runtime
                and not runtime._closed and not self.owner.parent._closed
                and str(runtime.context().get('session_id', '')) == session_id)

        async def pending() -> bool | None:
            async def read() -> bool | None:
                return (bool(agent.pending_inputs()) or agent.has_queued_messages()) if context_alive() else None
            try:
                return await bound.call(read)
            except (ValueError, RuntimeError):
                return None
        runtime.pending_messages = pending

        async def handler(extension: str, dialog: dict[str, Any]) -> bool:
            with self.owner._ui_lock:
                binding = self.owner._bindings.get(child)
                broker = self.owner._dialogs.get(child)
            if binding is None or broker is None:
                return False
            token, sink = binding
            generation, model = self.generation(token), agent.state.model
            sender = self.senders.get(token)
            def alive() -> bool:
                with self.owner._ui_lock:
                    return (context_alive() and self._attached(child, bound, binding)
                        and token in self.tokens and self.generation(token) == generation
                        and self.senders.get(token) is sender
                        and agent.is_streaming and agent.state.model == model
                        and bound.queue_alive is not None and bound.queue_alive()
                        and bound.admit is not None and bound.discard is not None)
            async def ask(name: str, projected: dict[str, Any]) -> Any:
                if not alive():
                    return False
                answer = await broker.ask(name, projected)
                return answer if alive() else False
            async def apply(identifier: str, content: str, delivery: Delivery,
                            images: tuple[ImageContent, ...]) -> None:
                async def enqueue() -> None:
                    if not alive():
                        return
                    if images:
                        if bound.validate_images is None:
                            raise ValueError('Child image admission unavailable')
                        await bound.validate_images(images)
                    if not alive() or bound.admit is None:
                        return
                    scope = {'child_id': child, 'child_owner': token, 'session_id': session_id,
                             'queued_input_generation': generation, 'extension': extension}
                    await bound.admit(identifier, content, delivery, images, alive)
                    bound.ids[identifier] = scope
                    event = {'type': 'child_extension_input_queued', **scope, 'id': identifier,
                             'content': content, 'delivery': delivery, 'image_count': len(images),
                             'notice': 'Queued after callback success; not consumed; permissions unchanged'}
                    try:
                        if not alive():
                            raise ValueError('Child queued-input owner changed during durable admission')
                        if sender is None:
                            sink(event)
                        else:
                            async with asyncio.timeout(10):
                                receipt = await sender(event)
                            if receipt is not None and receipt is not True:
                                raise ValueError('Child queued-input receipt was not delivered')
                        if not alive():
                            raise ValueError('Child queued-input owner changed before receipt settlement')
                    except BaseException:
                        if bound.discard is not None and await bound.discard(identifier):
                            bound.ids.pop(identifier, None)
                            try:
                                self._emit(child, bound, {'type': 'child_extension_input_returned', **scope,
                                    'id': identifier, 'removed': True,
                                    'notice': 'Receipt failed; owned unconsumed input removed; never replayed'})
                            except (OSError, RuntimeError, ValueError):
                                pass  # A closed transport cannot receive an additional negative receipt.
                        raise
                await bound.call(enqueue)
            return await reviewed(extension, dialog, ask=ask, apply=apply, alive=alive)

        def event(value: AgentEvent) -> None:
            if isinstance(value, InputConsumedEvent):
                scope = bound.ids.pop(value.input.id, None)
                if scope is not None:
                    self._emit(child, bound, {'type': 'child_extension_input_consumed', **scope, 'id': value.input.id})
            elif isinstance(value, AgentEndEvent):
                self._return(child, bound)
        bound.unsubscribe = agent.subscribe(event)
        self.handlers[child] = handler
        binding = self.owner._bindings.get(child)
        if binding is not None and binding[0] in self.tokens:
            runtime.queued_input_handler = handler

    def _emit(self, child: str, bound: _Bound, event: dict[str, Any]) -> None:
        binding = self.owner._bindings.get(child)
        if binding is not None and binding[0] == event['child_owner'] and self._attached(child, bound, binding):
            binding[1](event)

    def _return(self, child: str, bound: _Bound) -> None:
        # Do NOT drain here. ChildSupervisor._settle owns drain -> undelivered
        # -> durable record after AgentEndEvent. A UI notice is not recovery storage.
        for item in bound.agent.pending_inputs():
            scope = bound.ids.get(item.id)
            if scope is None:
                continue
            bound.ids.pop(item.id, None)
            self._emit(child, bound, {'type': 'child_extension_input_returned', **scope, 'id': item.id,
                'content': item.content, 'delivery': item.delivery, 'selected': item.selected,
                'images': [{'type': 'image', 'data': image.data, 'mime_type': image.mime_type}
                           for image in item.images],
                'notice': 'Unconsumed input remains for canonical child recovery; never replayed'})

    async def close(self, child: str) -> None:
        bound = self.bound.get(child)
        if bound is None:
            return
        async def finish() -> None:
            bound.unsubscribe()
            self._return(child, bound)
            # Cancellation need not emit AgentEndEvent. The canonical supervisor
            # drains before its finalize hook, so leftover IDs get a notice only;
            # their data remains in supervisor recovery, never a UI-owned cache.
            for identifier, scope in tuple(bound.ids.items()):
                self._emit(child, bound, {'type': 'child_extension_input_returned', **scope, 'id': identifier,
                    'uncertain': True,
                    'notice': 'No consumption receipt; inspect canonical child recovery/history; never replayed'})
            bound.ids.clear()
        try:
            await bound.call(finish)
        finally:
            self.bound.pop(child, None)
            self.handlers.pop(child, None)
            bound.runtime.queued_input_handler = None
            bound.runtime.pending_messages = None
