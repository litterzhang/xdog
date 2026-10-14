from __future__ import annotations

import asyncio

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.002)


def dialog(**options):
    return {'id': 'worker', 'view_id': 'view', 'kind': 'custom', 'title': 'Live overlay',
            'overlay': {'width': 40, 'non_capturing': True}, **options}


def resize(generation=0):
    return {'kind': 'resize', 'width': 40, 'height': 5, 'painted': -1, 'generation': generation}


def key(painted, generation=0):
    return {**resize(generation), 'kind': 'key', 'painted': painted, 'key': 'enter',
            'ctrl': False, 'alt': False, 'shift': False}


def test_noncapturing_implies_live_gate_and_explicit_live_controls_need_overlay():
    value = validate_dialog(dialog())
    assert value['overlay']['non_capturing'] is True and value['live_overlay'] is True
    for changes in ({'overlay': None, 'live_overlay': True}, {'live_overlay': 1}, {'fullscreen': True}):
        with pytest.raises(ValueError):
            validate_dialog(dialog(**changes))


async def test_api_control_admission_owner_quota_monotonic_and_closed_fencing():
    api, notices = ExtensionAPI({}), []
    api._overlay_notify = notices.append
    api._has_ui.set(True)
    api._has_custom.set(True)
    api._call_id.set('call')
    api._dialog_events['call'] = asyncio.Event()
    assert await api.custom('unavailable', lambda w, h: [], lambda e: None, overlay={'non_capturing': True}) is None
    assert not api.request_overlay('focus')
    api._has_overlay_controls.set(True)
    task = asyncio.create_task(api.custom('view', lambda w, h: [], lambda e: None,
                                         overlay={'non_capturing': True}))
    await until(lambda: 'call' in api._dialogs)
    for index in range(32):
        assert api.request_overlay(('hide', 'show', 'focus', 'unfocus')[index % 4])
    assert not api.request_overlay('focus')
    await asyncio.sleep(0)
    assert not notices and len(api._overlay_pending['call']) == 32
    api._custom_admitted.add('call')
    api._flush_overlay('call')
    assert len(notices) == 32 and [notice['sequence'] for notice in notices] == list(range(1, 33))
    api._call_id.set('foreign')
    assert not api.request_overlay('focus')
    api._call_id.set('call')
    with pytest.raises(ValueError):
        api.request_overlay('approve')
    api._custom_phase.set('render')
    with pytest.raises(ValueError):
        api.request_overlay('focus')
    api._custom_phase.set('')
    assert api.request_overlay('hide')
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not api.request_overlay('focus') and not api._overlay_pending
    assert len(notices) == 32  # Queued hide cannot mutate a closed invocation.


async def test_broker_controls_are_host_owned_replay_safe_and_revoke_queued_old_generations():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    async def render(value):
        return {'lines': ['data'], 'done': value['kind'] == 'key', 'value': False}
    task = asyncio.create_task(broker.ask_custom('fixture', dialog(overlay_state={
        'sequence': 2**53 - 1, 'hidden': True, 'capturing': True}), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert 'overlay_state' not in notices[0]['dialog']
    assert broker.request_custom(identifier, resize())
    await until(lambda: len(notices) == 2)
    assert broker.request_custom(identifier, key(1))
    assert not broker.control_custom('foreign', 'view', 1, 'focus')
    assert broker.control_custom('fixture', 'view', 1, 'hide')
    await until(lambda: len(notices) == 4)
    assert not task.done()
    assert notices[2]['state'] == {'sequence': 1, 'hidden': True, 'capturing': False}
    assert not broker.control_custom('fixture', 'view', 1, 'focus')
    assert not broker.control_custom('fixture', 'view', False, 'focus')
    assert broker.control_custom('fixture', 'view', 2, 'focus')
    assert broker.request_custom(identifier, key(2))
    await until(lambda: notices[-1].get('revision') == 3)
    assert not task.done()  # Even newly painted old-epoch frames cannot confirm control changes.
    assert broker.request_custom(identifier, resize(1))
    await until(lambda: notices[-1].get('revision') == 4)
    assert broker.request_custom(identifier, key(4, 1))
    assert await task is False
    assert not broker.control_custom('fixture', 'view', 3, 'focus')


async def test_old_background_tasks_cannot_adopt_sequential_replacement_view_and_nested_owner_is_nearest():
    api, notices = ExtensionAPI({}), []
    api._overlay_notify = notices.append
    api._render_notify = notices.append
    api._has_ui.set(True)
    api._has_custom.set(True)
    api._has_refresh.set(True)
    api._has_overlay_controls.set(True)
    api._call_id.set('root')
    api._dialog_events['root'] = asyncio.Event()
    task = asyncio.create_task(api.custom('first', lambda w, h: [], lambda e: None,
                                         overlay={'non_capturing': True}))
    await until(lambda: 'root' in api._dialogs)
    api._custom_admitted.add('root')
    admitted, resume = asyncio.Event(), asyncio.Event()
    async def background():
        assert api.request_overlay('focus') and api.request_render()
        admitted.set()
        await resume.wait()
        assert not api.request_overlay('hide') and not api.request_render()
    old = asyncio.create_task(background())
    await admitted.wait()
    api._reply('root', api._dialogs['root'][0]['id'], None)
    await task
    task = asyncio.create_task(api.custom('replacement', lambda w, h: [], lambda e: None,
                                         overlay={'non_capturing': True}))
    await until(lambda: 'root' in api._dialogs)
    resume.set()
    await old
    api._frame_owners['nested'] = 'root', api._dialogs['root'][0]['id']
    api._call_id.set('nested')
    api._dialog_events['nested'] = asyncio.Event()
    child = asyncio.create_task(api.custom('nested', lambda w, h: [], lambda e: None,
                                          overlay={'non_capturing': True}))
    await until(lambda: 'nested' in api._dialogs)
    api._custom_admitted.add('nested')
    api._frame_owners['input'] = 'nested', api._dialogs['nested'][0]['id']
    api._call_id.set('input')
    assert api.request_overlay('focus') and api.request_render()
    await asyncio.sleep(0)
    assert notices[-1]['call_id'] == 'nested' and notices[-2]['call_id'] == 'nested'
    api._reply('nested', api._dialogs['nested'][0]['id'], None)
    await child
    api._reply('root', api._dialogs['root'][0]['id'], None)
    await task
