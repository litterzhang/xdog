"""Canonical custom delivery on the existing Agent queues/continuation, never user input."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.agent import Agent
from xdog.agent.context_messages import ContextMessages
from xdog.agent.core import CustomAgentMessage
from xdog.agent.cross_loop import settle_on
from xdog.agent.extensions.context_message import TYPE, record


def bind(store: ContextMessages, agent: Agent,
         validate: Callable[[CustomAgentMessage], Awaitable[None]]) -> None:
    store.running = lambda: agent.is_streaming
    async def deliver(value: dict[str, Any], alive: Callable[[], bool], trigger: Any) -> None:
        value = record(value)
        message = CustomAgentMessage(TYPE, value)
        scope = store.scope()
        active, generation = agent.is_streaming, agent._cancel_token
        def current() -> bool:
            return (alive() and store.scope() == scope and store.eligible()
                    and ((not active and not agent.is_streaming)
                         or active and agent.is_streaming and agent._cancel_token == generation
                         and not agent.cancellation_requested))
        async def prepare() -> None:
            if not current():
                raise ValueError('Custom delivery owner changed')
            await validate(message)
        def rejected() -> None:
            store.publish({'type': 'extension_context_rejected', 'id': value['id'], 'session_id': scope[0],
                           'branch_id': scope[1], 'delivery': value['delivery']})
        async def persisted() -> None:
            try:
                if not current():
                    raise ValueError('Custom delivery owner changed before persistence')
                await store.persist(agent.messages)
            except BaseException:
                store.uncertain = True
                raise
            store.publish({'type': 'extension_context_admitted', **value})
        if active:
            loop = agent._cancel_loop
            if loop is None:
                raise ValueError('Original custom-delivery agent loop is unavailable')
            async def enqueue() -> None:
                await prepare()
                if not current():
                    raise ValueError('Original custom-delivery producer changed during admission')
                agent.queue_custom_message(message, value['delivery'], current=current, prepare=prepare,
                                           persist=persisted, rejected=rejected)
                store.publish({'type': 'extension_context_queued', 'id': value['id'], 'session_id': scope[0],
                               'branch_id': scope[1], 'delivery': value['delivery']})
            await settle_on(loop, enqueue())
            return  # trigger_turn never starts a competing active run.
        await prepare()
        if value.get('trigger_turn') and trigger is None and value['method'] == 'xdog/command':
            raise ValueError('Original host has no idle canonical continuation capability')
        if not current():
            raise ValueError('Idle custom owner changed')
        try:
            await store.persist((*agent.messages, message))
        except BaseException:
            store.uncertain = True
            raise
        store.publish({'type': 'extension_context_admitted', **value})
        if value.get('trigger_turn') and trigger is not None:
            await trigger(value)
    store.deliver = deliver
