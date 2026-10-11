"""Actual SDK/Claw persistence restart dedupes pending-to-canonical boundaries."""
from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.types import UserInput
from xdog.coding.core.agent_session import AgentSession

from packages.agent.tests.test_context_messages import row
from packages.claw.tests.test_context_messages import close as claw_close
from packages.claw.tests.test_context_messages import start as claw_start
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('boundary', ['pending', 'history-before-retirement'])
async def test_actual_host_restart_retains_pending_context_or_dedupes_committed_canonical(
    tmp_path, monkeypatch, host, boundary,
):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())
    if host == 'coding':
        owner, _ = session(tmp_path)
        owner.agent.set_stream_fn(stream)
        identity = owner.session_id
    else:
        server, runtime, owner, ui, task, writer = await claw_start(tmp_path, calls)
        identity = owner.meta.session_id
    try:
        await owner.context_messages.enqueue(row(session_id=identity), lambda: True)
        if boundary == 'history-before-retirement':
            original = owner.context_messages._write
            def fail_retire(scope, rows, current, replaced):
                if not rows:
                    raise OSError('Injected pending retirement failure after durable canonical history')
                return original(scope, rows, current, replaced)
            monkeypatch.setattr(owner.context_messages, '_write', fail_retire)
            with pytest.raises(OSError):
                await owner.context_messages.admit(await owner.context_messages.capture())
            assert owner.context_messages.uncertain
        assert not calls
        if host == 'coding':
            manager, settings = owner.session_manager, owner.settings
            await close(owner)
            saved = manager.load_session(identity)
            agent = Agent(stream, config=AgentConfig(model='offline/model'))
            agent.replace_messages(saved.messages)
            owner = AgentSession(agent=agent, session_data=saved, session_manager=manager, settings=settings,
                working_dir=tmp_path, tool_registry=None, bash=None)
            await owner.send_message('NEXT USER')
            await owner.send_message('SECOND USER')
            saved_messages = manager.load_session(identity).messages
        else:
            await claw_close(server, ui, task, writer)
            server, runtime, owner, ui, task, writer = await claw_start(tmp_path, calls)
            assert owner.meta.session_id == identity
            await server._orchestrator.route_message(UserInput(group_id='main', content='NEXT USER'))
            await server._orchestrator.route_message(UserInput(group_id='main', content='SECOND USER'))
            saved_messages = runtime.transcript_store.load_transcript(identity)
        assert len(calls) == 2
        assert all('EXPLICIT CUSTOM CONTENT' in str(call.messages) for call in calls)
        assert all('NOT MODEL INPUT' not in str(call.messages) for call in calls)
        assert sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages) == 1
        assert sum(isinstance(item, CustomAgentMessage) if host == 'coding' else item.get('role') == 'custom'
                   for item in saved_messages) == 1
        assert not (await owner.context_messages.capture())[1]
    finally:
        if host == 'coding':
            await close(owner)
        else:
            await claw_close(server, ui, task, writer)


async def test_custom_pending_image_obeys_current_model_gate_and_remains_pending(tmp_path):
    from dataclasses import asdict

    from packages.agent.tests.test_extension_queued_input import IMAGE
    owner, calls = session(tmp_path)
    try:
        value = row(session_id=owner.session_id, content=[asdict(IMAGE)])
        await owner.context_messages.enqueue(value, lambda: True)
        await owner.send_message('TEXT ONLY USER')
        assert not calls and not any(isinstance(item, CustomAgentMessage) for item in owner.agent.messages)
        assert not owner.context_messages.uncertain  # Known pre-write refusal, not a disk failure.
        assert (await owner.context_messages.capture())[1] == (value['id'],)
    finally:
        await close(owner)


@pytest.mark.parametrize('limit', ['allowed', 'media', 'bytes'])
async def test_context_image_uses_current_model_metadata_limits_before_canonical_admission(tmp_path, limit):
    from dataclasses import asdict

    from xdog.ai.types import Model, VisionLimits

    from packages.agent.tests.test_extension_queued_input import IMAGE
    owner, calls = session(tmp_path)
    try:
        owner.model_info = Model(id='vision-model', input=('text', 'image'), vision_limits=VisionLimits(
            max_prompt_image_size=1 if limit == 'bytes' else 1024,
            supported_media_types=('image/jpeg',) if limit == 'media' else ('image/png',)))
        value = row(session_id=owner.session_id, content=[asdict(IMAGE)])
        await owner.context_messages.enqueue(value, lambda: True)
        await owner.send_message('EXPLICIT USER')
        custom = [item for item in owner.agent.messages if isinstance(item, CustomAgentMessage)]
        assert bool(calls) is (limit == 'allowed') and bool(custom) is (limit == 'allowed')
        assert bool((await owner.context_messages.capture())[1]) is (limit != 'allowed')
    finally:
        await close(owner)
