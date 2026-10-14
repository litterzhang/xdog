from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.custom import answer, event, frame
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime


def request(kind='resize', *, width=40, height=5, painted=-1, **options):
    if kind == 'key':
        options = {'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False, **options}
    elif kind == 'paste':
        options = {'text': 'pasted\ntext', **options}
    return {'kind': kind, 'width': width, 'height': height, 'painted': painted, **options}


def dialog(**options):
    return {'id': 'worker', 'view_id': 'view-1', 'kind': 'custom', 'title': 'Custom view', **options}


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.001)


def test_invalid_events() -> None:

    def _check_case(changes):
        with pytest.raises(ValueError):
            event({**request(), **changes})

    for changes in [
        {"kind": "foreign"},
        {"width": True},
        {"width": 0},
        {"width": 10001},
        {"height": -1},
        {"height": 1001},
        {"painted": -2},
        {"painted": False},
        {"unknown": {}},
        {"text": "not resize"},
    ]:
        _check_case(changes)


def test_host_keys_are_reserved() -> None:

    def _check_case(key, ctrl):
        with pytest.raises(ValueError):
            event(request("key", key=key, ctrl=ctrl))

    for key, ctrl in [
        ("escape", False),
        ("Escape", False),
        ("c", True),
        ("C", True),
        ("d", True),
        ("l", True),
        ("z", True),
        ("\x03", False),
        ("\x1b", False),
    ]:
        _check_case(key, ctrl)


@pytest.mark.parametrize('value', [request('key', ctrl=1), request('key', key='x' * 129),
                                  request('key', key=''), request('paste', text='x' * 16001),
                                  request('paste', text=3)])
def test_invalid_input_shape(value):
    with pytest.raises(ValueError):
        event(value)


def test_codecs_snapshot_json_and_frame_and_preserve_false_zero_null():
    value = {'data': [False, 0, None]}
    snapshot = answer(value)
    value['data'].append('changed')
    assert snapshot == {'data': [False, 0, None]}
    raw = {'lines': ['界'], 'cursor': [0, 1], 'done': True, 'value': snapshot}
    result = frame(raw, request('key'))
    raw['lines'].append('changed')
    raw['cursor'][1] = 2
    assert result == {'lines': ['界'], 'cursor': [0, 1], 'done': True, 'value': snapshot}


@pytest.mark.parametrize('value', [float('nan'), {1: 'key'}, object(), (1, 2), 'x' * 65537],
                         ids=['nonfinite', 'nonstring-key', 'object', 'tuple', 'oversize'])
def test_bounded_json_answers(value):
    with pytest.raises(ValueError):
        answer(value)


def test_invalid_frames() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            frame(value, request("key"))

    for value in [
        {"lines": ["multiline\ntext"]},
        {"lines": ["carriage\rreturn"]},
        {"lines": ["x" * 8193]},
        {"lines": ["x"] * 201},
        {"lines": ["x" * 8192] * 17},
        {"lines": [False]},
        {"lines": [], "cursor": [0, 0]},
        {"lines": ["x"], "cursor": [True, 0]},
        {"lines": ["x"], "cursor": [0, 40]},
        {"lines": ["x"], "cursor": [5, 0]},
        {"lines": ["x"], "done": 1},
        {"lines": ["x"], "unknown": True},
        {"lines": ["x"], "done": True},
    ]:
        _check_case(value)


def test_resize_cannot_complete_and_zero_height_cannot_claim_cursor():
    with pytest.raises(ValueError):
        frame({'lines': ['x'], 'done': True, 'value': False}, request())
    with pytest.raises(ValueError):
        frame({'lines': ['x'], 'cursor': [0, 0]}, request(height=0))


def test_view_identity_is_required_and_bounded() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            validate_dialog(dialog(view_id=value))

    for value in [None, "", "x" * 129, "control\x1b", "界", 2]:
        _check_case(value)


async def test_custom_dialog_renderer_required_and_cannot_be_borrowed():
    broker = DialogBroker()
    with pytest.raises(ValueError):
        await broker.ask('fixture', dialog())
    with pytest.raises(ValueError):
        await broker.ask_custom('fixture', dialog(kind='input'), lambda value: None)
    with pytest.raises(ValueError):
        await broker.ask_custom('fixture', dialog(), object())


async def test_broker_stale_completion_requires_repaint_and_cannot_be_forged_as_answer():
    broker, notices, inputs = DialogBroker(), [], []
    broker.publish = notices.append

    async def render(value):
        inputs.append(value)
        return {'lines': ['data'], 'done': value['kind'] == 'key', 'value': {'choice': False}}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert identifier != 'worker'
    with pytest.raises(ValueError):
        broker.resolve(identifier, {'choice': True})
    assert broker.request_custom(identifier, request())
    await until(lambda: len(notices) == 2)
    assert notices[-1]['revision'] == 1
    assert broker.request_custom(identifier, request('key', painted=-1))
    await until(lambda: len(notices) == 3)
    assert notices[-1]['revision'] == 2 and notices[-1]['review_required']
    assert not task.done()
    # Even a current revision cannot complete against a changed viewport.
    assert broker.request_custom(identifier, request('key', width=39, painted=2))
    await until(lambda: len(notices) == 4)
    assert notices[-1]['revision'] == 3 and not task.done()
    assert broker.request_custom(identifier, request('key', width=39, painted=3))
    assert await task == {'choice': False}
    assert [item['kind'] for item in inputs] == ['resize', 'key', 'key', 'key']
    assert notices[-1]['type'] == 'extension_dialog_closed'
    assert not broker.custom.views and not broker.request_custom(identifier, request())


async def test_zero_height_cannot_complete_and_closed_broker_cannot_install_view():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append

    async def render(value):
        return {'lines': [], 'done': value['kind'] == 'key', 'value': False}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert broker.request_custom(identifier, request(height=0))
    await until(lambda: len(notices) == 2)
    assert broker.request_custom(identifier, request('key', height=0, painted=1))
    await until(lambda: len(notices) == 3)
    assert not task.done()
    broker.close()
    assert await task is None
    assert await broker.ask_custom('fixture', dialog(), render) is None
    assert not broker.custom.views


async def test_cross_thread_requests_are_snapshotted_fifo_and_bounded_including_inflight():
    broker, notices, received = DialogBroker(), [], []
    gate, started = asyncio.Event(), asyncio.Event()
    broker.publish = notices.append

    async def render(value):
        started.set()
        await gate.wait()
        received.append(value)
        return {'lines': ['frame']}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    payload = request('paste', text='original')
    assert await asyncio.to_thread(broker.request_custom, identifier, payload)
    payload['text'] = 'changed'
    await started.wait()
    for index in range(31):
        assert await asyncio.to_thread(broker.request_custom, identifier, request('paste', text=str(index)))
    assert not broker.request_custom(identifier, request())
    gate.set()
    await until(lambda: len(received) == 32)
    assert [item['text'] for item in received] == ['original', *(str(i) for i in range(31))]
    assert broker.custom.views[identifier].count == 0
    broker.close()
    assert await task is None


@pytest.mark.parametrize('mode', ['close', 'cancel', 'timeout', 'error'])
async def test_frame_tasks_settled_on_close_cancel_deadline_and_renderer_failure(mode):
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    started, settled = asyncio.Event(), asyncio.Event()

    async def render(value):
        try:
            started.set()
            if mode == 'error':
                raise ValueError('private failure must not reach UI')
            await asyncio.Event().wait()
        finally:
            settled.set()

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(timeout=0.1 if mode == 'timeout' else 300), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert broker.request_custom(identifier, request())
    await started.wait()
    view_task = broker.custom.views[identifier].task
    if mode == 'close':
        broker.close()
    elif mode == 'cancel':
        task.cancel()
    if mode == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is None
    assert settled.is_set() and view_task.done() and not broker.custom.views
    assert notices[-1] == {'type': 'extension_dialog_closed', 'id': identifier}
    assert 'private failure' not in str(notices)
    assert any(item['type'] == 'extension_custom_error' for item in notices) == (mode == 'error')


async def test_dead_loop_refused_without_leaking_quota():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append

    async def render(value):
        return {'lines': []}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    view = broker.custom.views[identifier]
    owner_loop = view.loop
    dead_loop = asyncio.new_event_loop()
    dead_loop.close()
    view.loop = dead_loop
    assert not broker.request_custom(identifier, request()) and view.count == 0
    view.loop = owner_loop
    broker.close()
    assert await task is None


@pytest.mark.parametrize('rendered', [{'lines': ['x'], 'done': True, 'value': True}, 'string'])
async def test_worker_render_cannot_propose_completion_or_rebind_an_existing_owner(rendered):
    api = ExtensionAPI({})
    api._call_id.set('owned')
    api._has_ui.set(True)
    api._has_custom.set(True)
    api._dialog_events['owned'] = asyncio.Event()
    task = asyncio.create_task(api.custom('Title', lambda width, height: rendered, lambda value: None))
    await api._dialog_events['owned'].wait()
    owned = api._dialogs['owned'][0]
    with pytest.raises(ValueError):
        await api.custom('Nested', lambda width, height: [], lambda value: None)
    assert 'owned' in api._custom_callbacks
    with pytest.raises(ValueError):
        await api._custom_frame('owned', owned['id'], request())
    with pytest.raises(ValueError):
        await api._custom_frame('owned', 'foreign', request())
    api._reply('owned', owned['id'], None)
    assert await task is None and not api._custom_callbacks


@pytest.mark.parametrize('mode', ['normal'])
async def test_real_worker_custom_callbacks_suspended_once_without_model_or_replay(tmp_path, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['custom'], root, tmp_path)
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    if mode != 'headless':
        runtime.custom_handler = broker.ask_custom
    try:
        task = asyncio.create_task(runtime.command('ext-custom-view', mode))
        if mode in {'normal', 'broken', 'cancel'}:
            await until(lambda: notices)
            identifier = notices[0]['dialog']['id']
            if mode == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not broker.custom.views and not broker.contains(identifier)
                assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
                return
            assert broker.request_custom(identifier, request())
            if mode == 'normal':
                await until(lambda: len(notices) == 2)
                assert broker.request_custom(identifier, request('paste', painted=1))
                await until(lambda: len(notices) == 3)
                assert broker.request_custom(identifier, request('key', painted=2))
        result = json.loads(await task)
        assert result['calls'] == 1
        if mode == 'normal':
            assert result['value'] == {'selected': False, 'count': 0, 'mode': None}
            assert result['inputs'] == ['pasted\ntext', 'enter']
            assert result['sizes'] == [[40, 5]] * 3
        else:
            assert result['value'] is None and result['inputs'] == []
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
        assert not broker.custom.views
    finally:
        broker.close()
        await runtime.aclose()


async def test_custom_frame_restores_original_callback_owner_when_commands_interleave():
    api = ExtensionAPI({})
    api._call_id.set('first')
    api._has_ui.set(True)
    api._has_custom.set(True)
    api._dialog_events['first'] = asyncio.Event()
    owners = []

    async def render(width, height):
        owners.append(api._call_id.get())
        with pytest.raises(ValueError):
            await api.input('Nested dialog must not borrow another owner')
        return ['data']

    task = asyncio.create_task(api.custom('Title', render, lambda value: None))
    await api._dialog_events['first'].wait()
    owned = api._dialogs['first'][0]
    api._call_id.set('second')
    api._dialog_events['second'] = asyncio.Event()
    assert (await api._custom_frame('first', owned['id'], request()))['lines'] == ['data']
    assert owners == ['first'] and api._call_id.get() == 'second' and 'second' not in api._dialogs
    api._reply('first', owned['id'], None)
    assert await task is None


async def test_dialog_only_host_cancels_custom_without_registering_callbacks():
    api = ExtensionAPI({})
    api._has_ui.set(True)
    assert await api.custom('Title', lambda width, height: [], lambda value: None) is None
    assert not api._custom_callbacks and not api._dialogs


async def test_loop_closing_during_scheduling_does_not_leak_queue_quota(monkeypatch):
    broker, notices = DialogBroker(), []
    broker.publish = notices.append

    async def render(value):
        return {'lines': []}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    view = broker.custom.views[identifier]

    def closed(*args):
        raise RuntimeError('Event loop closed between check and enqueue')

    with monkeypatch.context() as patch:
        patch.setattr(view.loop, 'call_soon_threadsafe', closed)
        assert not broker.request_custom(identifier, request()) and view.count == 0
    broker.close()
    assert await task is None


async def test_hung_worker_frame_is_reaped_after_view_deadline_without_replay(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['custom'], root, tmp_path, timeout=2)
    broker, notices = DialogBroker(), []
    broker.publish = notices.append

    async def short_view(extension, prompt, render):
        return await broker.ask_custom(extension, {**prompt, 'timeout': 0.1}, render)

    runtime.custom_handler = short_view
    try:
        await runtime.initialize()
        client = runtime._manager._clients['custom']
        from dataclasses import replace
        client.config = replace(client.config, timeout=0.5)
        process = client.process
        task = asyncio.create_task(runtime.command('ext-custom-view', 'hang'))
        await until(lambda: notices)
        identifier = notices[0]['dialog']['id']
        assert broker.request_custom(identifier, request())
        async with asyncio.timeout(6):
            with pytest.raises(TimeoutError):
                await task
        assert process.returncode is not None and client.process is None
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
        assert not broker.custom.views and not broker.contains(identifier)
        assert sum(item['type'] == 'extension_dialog' for item in notices) == 1
        assert notices[-1]['type'] == 'extension_dialog_closed'
    finally:
        broker.close()
        await runtime.aclose()


@pytest.mark.parametrize('generation', [False, -1, 2**53, '1'])
def test_invalid_custom_generations_are_not_coerced(generation):
    with pytest.raises(ValueError):
        event(request(generation=generation))


async def test_new_epoch_fences_queued_completion_even_when_geometry_unchanged():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    release, input_started = asyncio.Event(), asyncio.Event()

    async def render(value):
        if value['kind'] == 'key':
            input_started.set()
            await release.wait()
        return {'lines': ['data'], 'done': value['kind'] == 'key', 'value': True}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert broker.request_custom(identifier, request(generation=1))
    await until(lambda: len(notices) == 2)
    assert broker.request_custom(identifier, request('key', painted=1, generation=1))
    await input_started.wait()
    assert broker.request_custom(identifier, request(generation=2))
    release.set()
    await until(lambda: len(notices) == 4)
    assert not task.done() and notices[2]['review_required'] and notices[3]['generation'] == 2
    assert not broker.request_custom(identifier, request('key', painted=3, generation=1))
    assert broker.request_custom(identifier, request('key', painted=3, generation=2))
    assert await task is True


def test_custom_rich_option_declarations_are_strict() -> None:

    def _check_case(options):
        with pytest.raises(ValueError):
            validate_dialog(dialog(**options))

    for options in [
        {"fullscreen": 1},
        {"fullscreen": None},
        {"fullscreen": "true"},
        {"key_events": []},
        {"key_events": ["press", "press"]},
        {"key_events": ["unknown"]},
        {"key_events": [True]},
        {"key_events": "release"},
        {"key_events": ["press", "repeat", "release", "other"]},
    ]:
        _check_case(options)


def test_custom_key_option_snapshot_and_invalid_event_type():
    values = ['press', 'repeat', 'release']
    validated = validate_dialog(dialog(fullscreen=True, key_events=values))
    values.clear()
    assert validated['key_events'] == ['press', 'repeat', 'release'] and validated['fullscreen'] is True
    for value in (False, 'unknown', None):
        with pytest.raises(ValueError):
            event(request('key', event_type=value))
    assert event(request('key', event_type='release'))['event_type'] == 'release'


async def test_key_event_opt_in_is_enforced_before_worker_callback_delivery():
    broker, notices, received = DialogBroker(), [], []
    broker.publish = notices.append

    async def render(value):
        received.append(value)
        return {'lines': ['view']}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert not broker.request_custom(identifier, request('key', event_type='release'))
    assert not broker.request_custom(identifier, request('key', event_type='repeat'))
    assert not received
    assert broker.request_custom(identifier, request())
    await until(lambda: len(notices) == 2)
    broker.close()
    assert await task is None


def test_invalid_custom_overlay_placement_cannot_supply_host_callbacks_or_invalid_capture_values() -> None:

    def _check_case(overlay):
        with pytest.raises(ValueError, match="overlay"):
            validate_dialog(dialog(overlay=overlay))

    for overlay in [
        True,
        [],
        {"visible": "callback"},
        {"non_capturing": 1},
        {"width": 0},
        {"width": -1},
        {"width": True},
        {"width": "NaN%"},
        {"width": "101%"},
        {"width": "0%"},
        {"max_height": 10001},
        {"min_width": False},
        {"anchor": []},
        {"anchor": "unknown"},
        {"offset_x": 10001},
        {"offset_y": 0.5},
        {"row": "1.5%"},
        {"col": None},
        {"margin": True},
        {"margin": {"unknown": 0}},
        {"margin": {"top": -1}},
    ]:
        _check_case(overlay)


def test_valid_overlay_data_copies_nested_margins_preserves_placement_and_is_exclusive_with_fullscreen():
    from xdog.agent.extensions.custom import rich

    placement = {'width': '75%', 'min_width': 24, 'max_height': 12, 'row': '0%', 'col': 0,
                 'anchor': 'top-right', 'offset_x': -2, 'offset_y': 1, 'margin': {'right': 2}}
    validated = validate_dialog(dialog(overlay=placement))
    assert validated['overlay'] == placement and rich(validated)
    placement['margin']['right'] = 999
    assert validated['overlay']['margin']['right'] == 2
    assert validate_dialog(dialog(overlay={}))['overlay'] == {}
    with pytest.raises(ValueError, match='fullscreen and overlay'):
        validate_dialog(dialog(overlay={}, fullscreen=True))


async def test_render_finished_after_new_generation_is_explicitly_noninteractive():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    first_started, second_started = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()

    async def render(value):
        if value.get('generation') == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
            await release_second.wait()
        return {'lines': ['frame'], 'cursor': None}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    try:
        await until(lambda: notices)
        identifier = notices[0]['dialog']['id']
        assert broker.request_custom(identifier, request(generation=1))
        await first_started.wait()
        assert broker.request_custom(identifier, request(generation=2))
        release_first.set()
        await second_started.wait()
        frames = [notice for notice in notices if notice['type'] == 'extension_custom_frame']
        assert len(frames) == 1 and frames[0]['superseded'] is True and frames[0]['generation'] == 1
        assert not task.done()
        release_second.set()
        await until(lambda: len([notice for notice in notices if notice['type'] == 'extension_custom_frame']) == 2)
        assert notices[-1]['generation'] == 2 and not notices[-1].get('superseded', False)
    finally:
        release_first.set()
        release_second.set()
        broker.close()
        await task
