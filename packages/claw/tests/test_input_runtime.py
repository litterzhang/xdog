"""Durable application projection and actual Orchestrator turn admission."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope, decode_envelope, encode_envelope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import (
    ContextContinuation,
    ExternalInput,
    Group,
    GroupConfig,
    QueueMode,
    SystemInput,
    UserInput,
)

from packages.claw.tests.eyes_helpers import ScriptedModel


@pytest.mark.parametrize('kind', ['human', 'external', 'system', 'continuation'])
def test_host_projection_retains_kind_and_context_without_borrowing_payload_authority(tmp_path, kind):
    from xdog.claw.core.runtime.input_adapter import freeze_input, restore_input
    values = {
        'human': UserInput(group_id='main', content='original', sender='user-1', channel='weixin',
                           metadata={'context_token': 'private-route', 'nested': {'value': 1}}),
        'external': ExternalInput(group_id='main', content='ignored rendering', sender='acp', channel='acp',
            canonical=SessionInput('source-id', 'acp', 'human', 'event instruction', (),
                                   {'human_authorized': True})),
        'system': SystemInput(group_id='main', content='explicit obligation', metadata={'binding': 'opaque-host-data'}),
        'continuation': ContextContinuation(group_id='main', record_id='context-record-1'),
    }
    original = values[kind]
    saved = freeze_input(original, tmp_path, message_id='one')
    original.metadata['forged'] = True
    restored = restore_input(decode_envelope(encode_envelope(saved)))
    assert type(restored) is type(original)
    assert restored.group_id == 'main' and 'forged' not in restored.metadata
    if kind == 'human':
        assert restored.content == 'original' and restored.metadata['nested'] == {'value': 1}
        assert saved.human_authorized is True and saved.reply.recipient == 'user-1'
    elif kind == 'external':
        assert restored.canonical.id == 'one' and restored.canonical.instruction == 'event instruction'
        assert saved.human_authorized is False
    elif kind == 'continuation':
        assert restored.record_id == 'context-record-1'
    else:
        assert restored.content == 'explicit obligation' and saved.human_authorized is False


async def test_busy_ordinary_input_is_durable_and_never_implicitly_steered(tmp_path):
    model = ScriptedModel()
    entered, release = asyncio.Event(), asyncio.Event()
    model.gates['first'] = entered, release
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    orch.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=(),
                              config=GroupConfig(queue_mode=QueueMode.STEER, debounce_ms=0)))
    first = asyncio.create_task(orch.route_message(UserInput(group_id='main', content='first'), message_id='one'))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        accepted = asyncio.Event()
        assert await orch.route_message(UserInput(group_id='main', content='second'), message_id='two',
                                        on_accepted=accepted.set) is None
        assert accepted.is_set()
        service = await orch.input_service()
        scope = InputScope('main', tmp_path)
        assert (await service.store.get(scope, 'one')).status == 'running'
        assert (await service.store.get(scope, 'two')).status == 'pending'
        assert not orch._runtimes['main']._active_session.agent.pending_inputs()
        release.set()
        assert (await asyncio.wait_for(first, 3)).response_text == 'Scheduled report'
        assert (await asyncio.wait_for(service.wait(scope, 'two'), 3)).status == 'succeeded'
        assert model.calls == ['first', 'second']
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        await orch.stop()


async def test_runtime_capacity_rejects_without_losing_an_accepted_input(tmp_path):
    model = ScriptedModel()
    entered, release = asyncio.Event(), asyncio.Event()
    model.gates['first'] = entered, release
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    orch.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=(),
                              config=GroupConfig(max_queued_messages=2, debounce_ms=0)))
    first = asyncio.create_task(orch.route_message(UserInput(group_id='main', content='first'), message_id='one'))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await orch.route_message(UserInput(group_id='main', content='second'), message_id='two')
        with pytest.raises(ValueError, match='capacity'):
            await orch.route_message(UserInput(group_id='main', content='third'), message_id='three')
        service = await orch.input_service()
        scope = InputScope('main', tmp_path)
        assert [r.envelope.message.id for r in await service.store.records(scope)] == ['one', 'two']
        release.set()
        await first
        await asyncio.wait_for(service.wait(scope, 'two'), 3)
        assert model.calls == ['first', 'second']
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        await orch.stop()


async def test_runtime_recovers_pending_without_acp_or_tui_and_keeps_original_message_id(tmp_path):
    from xdog.claw.core.runtime.input_adapter import freeze_input
    value = freeze_input(UserInput(group_id='main', content='restore once'), tmp_path, message_id='pending')
    path = tmp_path / 'data' / 'inputs' / 'inbox.sqlite3'
    store = await InputStore.open(path)
    await store.enqueue(value)
    await store.close()
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    orch.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=()))
    try:
        await orch.start()
        service = await orch.input_service()
        result = await asyncio.wait_for(service.wait(value.scope, 'pending'), 3)
        assert result.status == 'succeeded' and result.session_id == orch.get_new_session_id('main')
        assert model.calls == ['restore once']
        assert (await service.submit(value))[1] is True
        assert model.calls == ['restore once']
    finally:
        await orch.stop()


@pytest.mark.parametrize('text', ['', '  \n\t'])
def test_image_only_projection_preserves_exact_blank_text_without_fabricating_a_prompt(tmp_path, text):
    import base64

    from xdog.ai.types import ImageContent
    from xdog.claw.core.runtime.input_adapter import freeze_input, restore_input
    image = ImageContent(data=base64.b64encode(b'\x89PNG\r\n\x1a\nexample').decode(), mime_type='image/png')
    original = UserInput(group_id='main', content=text, images=(image,))
    restored = restore_input(decode_envelope(encode_envelope(freeze_input(original, tmp_path, message_id='image'))))
    assert restored.content == text and restored.images == (image,)


async def test_terminal_duplicate_returns_receipt_without_running_model_or_retaining_another_view(tmp_path):
    import gc
    import weakref
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    orch.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=()))
    class View:
        def __call__(self, event):
            pass
    try:
        message = UserInput(group_id='main', content='once')
        first = await orch.route_message(message, message_id='same')
        view = View()
        reference = weakref.ref(view)
        second = await orch.route_message(message, message_id='same', on_display_event=view)
        assert first.response_text == second.response_text and model.calls == ['once']
        del view
        gc.collect()
        assert reference() is None, 'Terminal duplicate retained a stale presentation owner'
    finally:
        await orch.stop()
