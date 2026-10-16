from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker

FIXTURE = Path(__file__).parent / "fixtures" / "extensions"


async def test_broker_cross_thread_resolution_close_and_stale_ids():
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    task = asyncio.create_task(broker.ask("test", {"id": "worker", "kind": "confirm", "title": "Proceed"}))
    await asyncio.sleep(0)
    identifier = notices[0]["dialog"]["id"]
    assert identifier != "worker" and broker.contains(identifier)
    assert not broker.resolve("stale", True)
    with pytest.raises(ValueError):
        broker.resolve(identifier, "yes")
    broker.close()
    assert await task is False
    assert not broker.contains(identifier)


async def test_quarantine_cancels_waiting_questions_for_only_the_failed_extension():
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    first = asyncio.create_task(broker.ask("one", {"id": "a", "kind": "input", "title": "First"}))
    second = asyncio.create_task(broker.ask("two", {"id": "b", "kind": "input", "title": "Second"}))
    await asyncio.sleep(0)
    broker.cancel_extension("one")
    assert await first is None and not second.done()
    broker.close()
    assert await second is None


@pytest.mark.parametrize('outcome', ['accept', 'deny', 'late', 'timeout', 'cancel'])
async def test_basic_confirmation_has_typed_settlement_and_no_control_receipt(outcome):
    broker = DialogBroker()
    events = []
    broker.publish = events.append
    task = asyncio.create_task(broker.ask('owned', {
        'id': 'worker', 'kind': 'confirm', 'title': 'Question, not permission', 'timeout': 0.1}))
    await asyncio.sleep(0)
    identifier = events[0]['dialog']['id']
    if outcome == 'late':
        broker._pending[identifier][2].set_result(None)
        assert broker.resolve(identifier, True)
    elif outcome == 'cancel':
        task.cancel()
    elif outcome != 'timeout':
        assert await asyncio.to_thread(broker.resolve, identifier, outcome == 'accept')
    if outcome == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is (outcome == 'accept')
    assert not any(event['type'] == 'extension_dialog_accepted' for event in events)
    assert events[-1] == {'type': 'extension_dialog_closed', 'id': identifier}
    assert not broker.has_pending


@pytest.mark.parametrize('kind,default', [('input', None), ('select', None), ('confirm', False)])
async def test_basic_sdk_headless_questions_cancel_without_implicit_default_or_host_authority(kind, default):
    from xdog.agent.extensions.api import ExtensionAPI
    api = ExtensionAPI({})
    options = {'choices': ['first', 'second']} if kind == 'select' else {}
    answer = await getattr(api, kind)('Question', **options)
    assert answer is default and not api._dialogs and not api._dialog_events


async def test_basic_sdk_late_reply_never_answers_successor_question_on_same_callback():
    from xdog.agent.extensions.api import ExtensionAPI
    api = ExtensionAPI({})
    api._has_ui.set(True)
    api._call_id.set('original')
    api._dialog_events['original'] = asyncio.Event()
    first = asyncio.create_task(api.input('First'))
    await asyncio.sleep(0)
    original = api._dialogs['original'][0]['id']
    with pytest.raises(ValueError):
        api._reply('foreign', original, 'forged')
    assert not first.done()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not api._dialogs
    second = asyncio.create_task(api.confirm('Second'))
    await asyncio.sleep(0)
    replacement = api._dialogs['original'][0]['id']
    assert replacement != original
    with pytest.raises(ValueError):
        api._reply('original', original, True)
    assert not second.done()
    with pytest.raises(ValueError):
        api._reply('original', replacement, 'yes')
    assert not second.done()
    api._reply('original', replacement, None)
    assert await second is False
    assert not api._dialogs
