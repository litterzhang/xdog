"""Real files → independent Eyes → ACP → sole owner or durable offline inbox."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from packages.eyes.tests.test_runtime import wait_for


@pytest.fixture
def coding_session(tmp_path, host_skill_manager):
    """Eyes-owned host fixture: real SDK skills, no changes to Coding's fixture."""
    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager

    manager = SessionManager(sessions_dir=tmp_path / 'sessions')
    data = manager.create_session(model='test', working_dir=tmp_path)
    session = AgentSession(
        agent=Agent(lambda *_: EventStream.empty(AssistantMessage(content=())),
                    config=AgentConfig(model='test'), skills=host_skill_manager),
        session_data=data, session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'),
        tool_registry=None, bash=None, working_dir=tmp_path,
    )
    yield session
    session.dispose()


def definition(data,address,sid,root,delivery):
    from copy import deepcopy

    result = deepcopy(data)
    result['sources'][0].update(root=str(root),include=['src/**'],poll_interval_ms=50)
    result['detectors'][0].update(debounce_ms=50,max_batch_delay_ms=100)
    result['handlers'][0].update(connection={'transport':'unix','path':str(address)},session=sid,delivery=delivery)
    return result


async def test_file_to_acp_coding_turn_then_self_edit_is_automatically_consumed(
    tmp_path, observer_data, coding_session, assert_host_goal_discoverable,
):
    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.interactive.components.chat_log import ChatLog
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter, capture_history
    from xdog.coding.modes.interactive.theme import create_default_theme
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.tui.utils import strip_ansi

    (tmp_path/'src').mkdir()
    changed = tmp_path/'src'/'a.py'
    turns = []

    def model(*args):
        turns.append(args)
        changed.write_text('agent edit')
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Reviewed change'),)))

    coding_session.agent.set_stream_fn(model)
    owner = SessionRuntime(coding_session,bound=True)
    owner.start()
    await owner.open_endpoint()
    runtime = ObserverRuntime(runtime_location(tmp_path))
    identifier = (await runtime.start(definition(observer_data,owner.record.address,coding_session.session_id,
                                                tmp_path,'prompt')))['registration_id']
    try:
        await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
        changed.write_text('human edit')
        await wait_for(lambda:len(turns)==1)
        await wait_for(lambda:len(owner.controller.store.records())==2)
        await wait_for(lambda:len(turns)==2 and owner.controller.store.records()[-1].status=='succeeded')
        assert changed.read_text()=='agent edit'
        for turn in turns:
            assert_host_goal_discoverable(turn[1])
        log = ChatLog(create_default_theme())
        snapshot = await capture_history(coding_session)
        SessionPresenter(log).replay(snapshot.records,snapshot.messages,snapshot.inbox)
        output = '\n'.join(strip_ansi(line) for line in log.render(120))
        assert 'Eyes' in output and 'Review tests' in output and 'a.py' in output
        assert 'Reviewed change' in output and 'succeeded' in output
        assert sum(getattr(m,'type','')=='xdog.session_input.v1' for m in coding_session.messages)==2
    finally:
        await runtime.stop_entry(identifier)
        await asyncio.wrap_future(owner.close())


async def test_five_offline_callbacks_automatically_form_one_tui_batch_and_survive_owner_exit(
    tmp_path, observer_data, host_skill_manager, assert_host_goal_discoverable,
):
    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.settings_manager import SettingsManager
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.tui.utils import strip_ansi

    (tmp_path/'src').mkdir()
    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(model='test',working_dir=tmp_path)
    store = InboxStore(manager._dir,tmp_path/'inbox')
    turns = []

    def model(*args):
        turns.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='OFFLINE-BATCH-ANSWER'),)))

    with tempfile.TemporaryDirectory(prefix='eyes-offline-') as short:
        ingress = await IngressServer.start(IngressConfig(manager._dir,store.root,Path(short)/'acp.sock'))
        runtime = ObserverRuntime(runtime_location(tmp_path))
        identifier = (await runtime.start(definition(observer_data,ingress.config.address,data.session_id,
                                                    tmp_path,'enqueue')))['registration_id']
        ui = None
        try:
            await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
            changed = tmp_path/'src'/'a.py'
            for index in range(5):
                changed.write_text(f'offline edit {index}')
                await wait_for(lambda:len(store.records(data.session_id,tmp_path))==index+1)
            assert {row.status for row in store.records(data.session_id,tmp_path)}=={'pending'}
            assert not turns and not manager.load_session(data.session_id).messages
            session = AgentSession(agent=Agent(model,config=AgentConfig(model='test'), skills=host_skill_manager),
                session_data=manager.load_session(data.session_id),session_manager=manager,
                settings=SettingsManager(global_path=tmp_path/'settings.json'),
                working_dir=tmp_path,bash=None,tool_registry=None)
            ui = InteractiveMode(session)
            await asyncio.to_thread(ui._session_events.start)
            ui._replay_history()
            await wait_for(lambda:len(turns)==1 and len(session.runtime.controller.store.records())==5
                and all(row.status=='succeeded' for row in session.runtime.controller.store.records()))
            assert_host_goal_discoverable(turns[0][1])
            assert len(turns[0][1].messages)==5 and all(isinstance(m,UserMessage) for m in turns[0][1].messages)
            assert session.permissions.mode=='ask' and not session.permissions.has_pending_requests
            while not ui._event_queue.empty():
                ui._poll()
            output = '\n'.join(strip_ansi(line) for line in ui._chat_log.render(120))
            assert output.count('External input')==5 and output.count('OFFLINE-BATCH-ANSWER')==1
            assert 'Eyes' in output and 'a.py' in output
            rows = store.records(data.session_id,tmp_path)
            batch_id, = {row.batch_id for row in rows}
            assert batch_id and len({row.request_id for row in rows})==5
            assert len({row.message.message_id for row in rows})==5
            from xdog.coding.modes.interactive.components.chat_log import ChatLog
            from xdog.coding.modes.interactive.session_presenter import SessionPresenter
            from xdog.coding.modes.interactive.theme import create_default_theme
            saved = manager.load_session(data.session_id)
            replay = ChatLog(create_default_theme())
            SessionPresenter(replay).replay(session.runtime.controller.store.records(),tuple(saved.messages),rows)
            reopened = '\n'.join(strip_ansi(line) for line in replay.render(120))
            assert reopened.count('External input')==5 and reopened.count('OFFLINE-BATCH-ANSWER')==1
            ui._request_exit()
            await asyncio.to_thread(ui._session_events.close)
            ui = None
            assert ingress.server.is_serving() and not runtime.entries[identifier].registration.task.done()
            changed.write_text('after TUI exit')
            await wait_for(lambda:len(store.records(data.session_id,tmp_path))==6)
            assert len(turns)==1
            assert any(row.status=='pending' for row in store.records(data.session_id,tmp_path))
            await runtime.stop_entry(identifier)
            assert len(store.records(data.session_id,tmp_path))==6 and ingress.server.is_serving()
        finally:
            if ui is not None:
                ui._request_exit()
                await asyncio.to_thread(ui._session_events.close)
            if identifier in runtime.entries:
                await runtime.stop_entry(identifier)
            await ingress.aclose()


async def test_receiver_disconnect_pauses_collection_and_requires_explicit_same_store_resume(tmp_path,observer_data):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    (tmp_path/'src').mkdir()
    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(working_dir=tmp_path)
    store = InboxStore(manager._dir,tmp_path/'inbox')
    with tempfile.TemporaryDirectory(prefix='eyes-receiver-') as short:
        config = IngressConfig(manager._dir,store.root,Path(short)/'acp.sock')
        ingress = await IngressServer.start(config)
        runtime = ObserverRuntime(runtime_location(tmp_path))
        serving = asyncio.create_task(runtime.serve())
        identifier = (await runtime.start(definition(observer_data,config.address,data.session_id,
                                                    tmp_path,'enqueue')))['registration_id']
        try:
            await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
            await ingress.aclose()
            await wait_for(lambda:runtime.entries[identifier].suspended.is_set())
            assert (await runtime.status(identifier))['registrations'][0]['state']=='paused'
            (tmp_path/'src'/'a.py').write_text('while receiver offline')
            await asyncio.sleep(0.2)
            assert not store.records(data.session_id,tmp_path)
            ingress = await IngressServer.start(config)
            await asyncio.sleep(0.25)
            assert runtime.entries[identifier].suspended.is_set()  # No automatic reconnect.
            assert await runtime.resume_entry(identifier)
            await wait_for(lambda:len(store.records(data.session_id,tmp_path))==1)
            assert not manager.load_session(data.session_id).messages
        finally:
            if identifier in runtime.entries:
                await runtime.stop_entry(identifier)
            runtime.stop.set()
            await asyncio.wait_for(serving,3)
            await ingress.aclose()


def current_inputs(context):
    """Current independent user inputs after the last completed assistant message."""
    from xdog.ai.types import AssistantMessage
    messages = context.messages
    boundary = max((i for i,message in enumerate(messages) if isinstance(message,AssistantMessage)),default=-1)
    return messages[boundary+1:]


async def test_online_file_callbacks_form_next_five_message_batch_and_freeze_selection(
    tmp_path,observer_data,coding_session,assert_host_goal_discoverable,
):
    from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    (tmp_path/'src').mkdir()
    changed = tmp_path/'src'/'a.py'
    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    calls = []
    entered = {i:asyncio.Event() for i in (1,2)}
    release = {i:asyncio.Event() for i in (1,2)}

    def model(_,context,options):
        calls.append(context)
        number = len(calls)
        message = AssistantMessage(content=(TextContent(text=f'Batch {number} complete'),))
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)
        async def generate():
            yield StartEvent(partial=AssistantMessage())
            if number in entered:
                entered[number].set()
                await release[number].wait()
            yield DoneEvent(message=message)
        return EventStream(generate(),result)

    coding_session.agent.set_stream_fn(model)
    owner = SessionRuntime(coding_session,bound=True,inbox_store=store)
    owner.start()
    with tempfile.TemporaryDirectory(prefix='eyes-batch-') as short:
        ingress = await IngressServer.start(IngressConfig(store.sessions_dir,store.root,Path(short)/'acp.sock'))
        runtime = ObserverRuntime(runtime_location(tmp_path))
        identifier = (await runtime.start(definition(observer_data,ingress.config.address,coding_session.session_id,
                                                    tmp_path,'enqueue')))['registration_id']
        try:
            await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
            changed.write_text('trigger')
            await asyncio.wait_for(entered[1].wait(),5)
            for index in range(5):
                changed.write_text(f'queued while online {index}')
                await wait_for(lambda:len(store.records(coding_session.session_id,tmp_path))==index+2)
            assert len(calls)==1
            assert [row.status for row in store.records(coding_session.session_id,tmp_path)]==['running']+['pending']*5
            release[1].set()
            await asyncio.wait_for(entered[2].wait(),5)
            assert len(current_inputs(calls[1]))==5
            selected = owner.controller._batch
            assert selected is not None and len(selected.items)==5
            changed.write_text('arrived after selection')
            await wait_for(lambda:len(store.records(coding_session.session_id,tmp_path))==7)
            late = store.records(coding_session.session_id,tmp_path)[-1]
            assert late.status=='pending' and not late.batch_id and len(calls)==2
            assert owner.controller._batch==selected
            release[2].set()
            await wait_for(lambda:len(owner.controller.store.records())==7
                and all(row.status=='succeeded' for row in owner.controller.store.records()))
            for context in calls:
                assert_host_goal_discoverable(context)
            assert [len(current_inputs(context)) for context in calls]==[1,5,1]
            assert all(isinstance(message,UserMessage) for context in calls for message in current_inputs(context))
            rows = store.records(coding_session.session_id,tmp_path)
            assert {row.batch_id for row in rows[1:6]}=={selected.id}
            assert len({rows[0].batch_id,selected.id,rows[-1].batch_id})==3
            assert coding_session.permissions.mode=='ask' and not coding_session.permissions.has_pending_requests
        finally:
            for event in release.values():
                event.set()
            await runtime.stop_entry(identifier)
            await asyncio.wrap_future(owner.close())
            await ingress.aclose()


async def sdk_file_batch(tmp_path,observer_data,route_count,host_skill_manager,assert_host_goal_discoverable):
    from copy import deepcopy

    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.settings_manager import SettingsManager
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    (tmp_path/'src').mkdir()
    changed = tmp_path/'src'/'a.py'
    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(model='test',working_dir=tmp_path)
    store = InboxStore(manager._dir,tmp_path/'inbox')
    calls = []
    def model(_,context,options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='SDK batch complete'),)))

    with tempfile.TemporaryDirectory(prefix='eyes-sdk-') as short:
        ingress = await IngressServer.start(IngressConfig(manager._dir,store.root,Path(short)/'acp.sock'))
        runtime = ObserverRuntime(runtime_location(tmp_path))
        spec = definition(observer_data,ingress.config.address,data.session_id,tmp_path,'enqueue')
        if route_count==2:
            spec['routes'].append({**deepcopy(spec['routes'][0]),'id':'independent-review'})
        identifier = (await runtime.start(spec))['registration_id']
        session = None
        try:
            await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
            changes = 5 if route_count==1 else 2
            for index in range(changes):
                changed.write_text(f'offline evidence {index}')
                await wait_for(lambda:len(store.records(data.session_id,tmp_path))==(index+1)*route_count)
            expected = changes*route_count
            assert not calls and {row.status for row in store.records(data.session_id,tmp_path)}=={'pending'}
            session = AgentSession(agent=Agent(model,config=AgentConfig(model='test'), skills=host_skill_manager),
                session_data=manager.load_session(data.session_id),session_manager=manager,
                settings=SettingsManager(global_path=tmp_path/'settings.json'),
                working_dir=tmp_path,bash=None,tool_registry=None)
            assert await session.send_message('Process all pending evidence') is not None
            assert len(calls)==1 and len(current_inputs(calls[0]))==expected+1
            assert_host_goal_discoverable(calls[0])
            assert all(isinstance(message,UserMessage) for message in current_inputs(calls[0]))
            rows = store.records(data.session_id,tmp_path)
            assert len(rows)==expected and {row.status for row in rows}=={'succeeded'}
            assert len({row.batch_id for row in rows})==1
            if route_count==2:
                assert len({row.message.provenance['route_id'] for row in rows})==2
            assert session.runtime is None
            # Installed session capabilities retain this sole callback owner;
            # observer intake does not construct another session or execution loop.
            controller = session._sdk_controller
            assert controller is not None and session._capability_loop is controller._loop
            assert not session.children.runtime.active
            assert session.permissions.mode=='ask' and not session.permissions.has_pending_requests
        finally:
            if session is not None:
                controller = session._sdk_controller
                session.dispose()
                if controller is not None:
                    await asyncio.wrap_future(controller.close())
            await runtime.stop_entry(identifier)
            await ingress.aclose()


async def test_sdk_only_owner_consumes_five_file_callbacks_without_message_approval(
    tmp_path, observer_data, host_skill_manager, assert_host_goal_discoverable,
):
    await sdk_file_batch(tmp_path,observer_data,1,host_skill_manager,assert_host_goal_discoverable)


async def test_two_routes_deliver_independent_callbacks_without_a_shared_wake_budget(
    tmp_path, observer_data, host_skill_manager, assert_host_goal_discoverable,
):
    await sdk_file_batch(tmp_path,observer_data,2,host_skill_manager,assert_host_goal_discoverable)
