"""A failed event consumer cannot release a still-running application producer."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.inputs.models import InputScope

from packages.claw.tests.test_orchestrator import _progress_message
from packages.claw.tests.test_weixin_application_intake import application, text
from packages.claw.tests.test_weixin_monitor import wait


@pytest.mark.parametrize('persist_failure', [False, True])
async def test_output_journal_failure_settles_agent_before_terminal_receipt_and_group_release(
    tmp_path, monkeypatch, persist_failure,
):
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('read-only evidence')
    second_entered, cleaning, release_cleanup, salvage = (asyncio.Event() for _ in range(4))
    calls = []
    final = AssistantMessage(content=(TextContent(text='must not escape the failed turn'),))

    def model(model_id, context, options=None):
        index = len(calls)
        calls.append(context)
        message = _progress_message('Checking.', evidence, 'read-evidence') if index == 0 else final
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)

        async def generate():
            if index:
                second_entered.set()
                try:
                    await salvage.wait()
                finally:
                    cleaning.set()
                    await release_cleanup.wait()
            yield StartEvent(partial=message)
            yield DoneEvent(message=message)

        return EventStream.from_async_generator(generate(), result_future=result)

    orch, channel, api = await application(tmp_path, model, enabled_tools=('filesystem',))
    outputs = await orch.output_service()
    actual_append = outputs.store.append
    injected = []

    async def append(*args, **kwargs):
        if not injected:
            injected.append(True)
            await second_entered.wait()
            if persist_failure:
                session = orch._runtimes['main']._active_session

                def fail_persist(*args, **kwargs):
                    assert not session.agent.is_streaming, 'Persistence preceded producer settlement'
                    raise OSError('cleanup transcript persistence failure')

                monkeypatch.setattr('xdog.claw.core.persistence.transcript_commit.commit', fail_persist)
            raise OSError('original output journal failure')
        return await actual_append(*args, **kwargs)

    monkeypatch.setattr(outputs.store, 'append', append)
    receipt_task = cleaning_task = None
    try:
        await channel._accept_inbound(text(1, 'alice', 'Read the evidence'))
        inputs = await orch.input_service()
        scope = InputScope('main', tmp_path)
        submitted, = await inputs.store.records(scope)
        receipt_task = asyncio.create_task(inputs.wait(scope, submitted.envelope.message.id))
        cleaning_task = asyncio.create_task(cleaning.wait())
        await wait(asyncio.wait((receipt_task, cleaning_task), return_when=asyncio.FIRST_COMPLETED))
        assert cleaning.is_set(), 'Failed consumer published a terminal receipt without stopping its producer'
        assert not receipt_task.done(), 'Receipt settled before the actual model cleanup'
        assert orch.group_controller('main').busy
        session = orch._runtimes['main'].get_or_create_session(allow_reset=False)
        assert session.agent.is_streaming
        current, = await inputs.store.records(scope)
        assert current.status == 'running'
        release_cleanup.set()
        receipt = await wait(receipt_task)
        assert receipt.status == 'failed'
        if persist_failure:
            assert 'original output journal failure' in receipt.error
            assert 'cleanup transcript persistence failure' in receipt.error
            durable = await inputs.store.get(scope, submitted.envelope.message.id)
            assert durable is not None and durable.error == receipt.error
            assert session._history_fault and session.context_messages.uncertain
        else:
            assert receipt.error == 'original output journal failure'
        assert not session.agent.is_streaming and not orch.group_controller('main').busy
        assert len(calls) == 2
        notices = []
        while not api.sent.empty():
            notices.append(api.sent.get_nowait())
        assert all('must not escape' not in str(notice) for notice in notices)
        assert not any('must not escape' in str(message.content) for message in session.agent.messages)
        saved = session._store.load_transcript(session.meta.session_id)
        if not persist_failure:
            assert 'Checking.' in str(saved)
        assert 'must not escape' not in str(saved)
    finally:
        salvage.set()
        release_cleanup.set()
        if cleaning_task is not None:
            cleaning_task.cancel()
        if receipt_task is not None:
            await asyncio.gather(receipt_task, return_exceptions=True)
        if cleaning_task is not None:
            await asyncio.gather(cleaning_task, return_exceptions=True)
        await orch.stop()
