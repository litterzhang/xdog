"""Opt-in reviewed extension inputs belong to one running RPC producer, never a draft."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xdog.agent.events import InputConsumedEvent
from xdog.ai.types import ImageContent

if TYPE_CHECKING:
    from xdog.coding.modes.rpc.extensions import RpcExtensions


class RpcQueuedInputs:
    def __init__(self, host: RpcExtensions) -> None:
        self.host = host
        self.enabled = False
        self.generation = 0
        self._owned: dict[str, tuple[str, str, str]] = {}

    def negotiate(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.generation += 1
        self.enabled = enabled
        if not enabled:
            self.host.session.extension_dialogs.cancel_where(lambda row: row.get('operation') == 'queue_input')
            # Already admitted input is not replayed/canceled by a later grant
            # toggle. Normal prompt cancellation still owns producer settlement.

    async def ask(self, extension: str, dialog: dict[str, Any]) -> bool:
        from xdog.agent.extensions.invocation import current_invocation
        from xdog.agent.extensions.queued_input import reviewed
        from xdog.ai.utils.image_files import validate_references
        host, captured, generation = self.host, self.host.scope, self.generation
        invocation = current_invocation()
        if captured is None or invocation is None or invocation.child_id:
            return False
        agent = host.session.agent
        revision = host.navigation.revision if host.navigation is not None else None
        def alive() -> bool:
            return (self.enabled and self.generation == generation and not host.closed
                and host.scope == captured and host.control is None and host.session.session_id == captured[0]
                and host.session.agent is agent and agent.is_streaming
                and (host.navigation.revision if host.navigation is not None else None) == revision
                and not host.session.permissions.has_pending_requests
                and not any(manager.has_pending_requests for manager in host.session.children.permissions.values()))
        async def review(name: str, projected: dict[str, Any]) -> Any:
            if not alive():
                return False
            answer = await host.session.extension_dialogs.ask(name, projected)
            return answer if alive() else False
        async def apply(identifier: str, content: str, delivery: str, images: tuple[ImageContent, ...]) -> None:
            import asyncio
            from typing import cast

            from xdog.agent import Delivery
            if not alive():
                return
            if images:
                await asyncio.to_thread(validate_references, images, host.session.model_info)
            if not alive() or len(agent.pending_inputs()) >= 50:
                return
            agent.queue_input(identifier, content, cast(Delivery, delivery), images)
            self._owned[identifier] = captured
            payload = {'type': 'extension_input_queued', 'id': identifier, 'request_id': captured[1],
                'session_id': captured[0], 'scope': captured[2], 'extension': extension,
                'content': content, 'delivery': delivery, 'image_count': len(images),
                'notice': 'Queued after callback success; not proof of consumption; permissions unchanged'}
            try:
                if host.send is not None:
                    async with asyncio.timeout(10):
                        await host.send(payload)
                else:
                    host.emit(payload)
            except BaseException:
                # Producer is still awaiting this callback; if removal succeeds
                # no failed delivery can become an invisible later model input.
                if agent.change_input(identifier, 'remove'):
                    self._owned.pop(identifier, None)
                raise
        async def owned_apply(identifier: str, content: str, delivery: str,
                              images: tuple[ImageContent, ...]) -> None:
            from xdog.agent.supervision import settle_on
            await settle_on(host.loop, apply(identifier, content, delivery, images))
        return await reviewed(extension, dialog, ask=review, apply=owned_apply, alive=alive)

    def event(self, event: Any) -> None:
        if isinstance(event, InputConsumedEvent):
            captured = self._owned.pop(event.input.id, None)
            if captured is not None and not self.host.closed:
                self.host.emit({'type': 'extension_input_consumed', 'id': event.input.id,
                    'request_id': captured[1], 'session_id': captured[0], 'scope': captured[2]})

    def finish(self, captured: tuple[str, str, str]) -> None:
        host = self.host
        # Only this invocation's generated IDs; never drain another client's
        # pending text or borrow a parent draft. Returned input is not replayed.
        for identifier, owner in tuple(self._owned.items()):
            if owner != captured:
                continue
            self._owned.pop(identifier, None)
            for item in host.session.agent.drain_inputs(identifier):
                if not host.closed:
                    host.emit({'type': 'extension_input_returned', 'id': item.id,
                        'request_id': captured[1], 'session_id': captured[0], 'scope': captured[2],
                        'content': item.content, 'delivery': item.delivery, 'selected': item.selected,
                        'images': [{'type': 'image', 'data': image.data, 'mime_type': image.mime_type}
                                   for image in item.images],
                        'notice': 'Producer settled; inspect history before explicit retry; never replayed'})
