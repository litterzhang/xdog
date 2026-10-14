"""Reviewed extension navigation queues only on successful callback, then reuses the native staged owner."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, Model, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation


class Adapter:
    def __init__(self):
        self.calls = []
        self.profile = Model(id='offline/model', context_window=128000)
    def resolve_model(self, name):
        return self.profile if name == self.profile.id else None
    def models(self):
        return (self.profile,)
    def __call__(self, model, context, options):
        self.calls.append(context)
        return EventStream.empty(AssistantMessage())


def setup(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/navigator', root / 'navigator')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root),
        'extension_settings': {'navigator': {'marker': str(tmp_path / 'queued'),
                                           'release': str(tmp_path / 'release')}}}))
    store = SessionManager(tmp_path / 'sessions')
    adapter = Adapter()
    owner = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/model')),
        session_data=store.create_session(working_dir=tmp_path, model='offline/model'), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))
    owner.model_info = adapter.profile
    owner.agent.replace_messages([UserMessage(content='ORIGINAL'),
                                  AssistantMessage(content=(TextContent(text='answer'),))])
    owner._persist()
    return owner, store, adapter


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def response(event, value):
    return {'type': 'extension_dialog_response', 'id': event['dialog']['id'], 'value': value,
        'request_id': event['request_id'], 'session_id': event['session_id'], 'scope': event['scope']}


@pytest.mark.parametrize('kind', ['navigation', 'editor'])
async def test_transport_answer_that_loses_to_a_completed_canceled_broker_future_cannot_create_host_grant(
    tmp_path, kind,
):
    owner, _, adapter = setup(tmp_path)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_navigation': 1, 'extension_editor': 1})
    controller.control = 'race', owner.session_id
    try:
        async with controller.bind('race'):
            if kind == 'navigation':
                request = {'kind': 'session', 'id': 'worker', 'title': 'Review target', 'operation': 'navigate',
                    'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
                proposed = True
            else:
                request = {'kind': 'editor', 'id': 'worker', 'title': 'Read editor', 'operation': 'read'}
                proposed = {'text': 'OLD-DRAFT', 'cursor': 9, 'image_count': 0, 'revision': 'a' * 64}
            waiting = asyncio.create_task(controller._ask('navigator', request))
            async with asyncio.timeout(5):
                while not any(event['type'] == 'extension_dialog' for event in events):
                    await asyncio.sleep(0.003)
            event = next(event for event in events if event['type'] == 'extension_dialog')
            # Real broker future already resolved as cancellation, but its coroutine
            # has not yet removed the ID. A transport ACK is not the callback's value.
            future = owner.extension_dialogs._pending[event['dialog']['id']][2]
            future.set_result(None)
            with pytest.raises(ValueError, match='owns input'):
                controller.answer(response(event, proposed))
            assert await waiting is None
            assert controller.deferred_navigation is None and not controller.editor_revisions
            assert navigation.task is None and not adapter.calls
    finally:
        await navigation.aclose()
        await controller.aclose()
        await close(owner)
