"""Real files → independent Eyes → ACP → sole owner or durable offline inbox."""

import asyncio
import tempfile
from pathlib import Path

from packages.coding.tests.conftest import coding_session as session_fixture
from packages.eyes.tests.test_runtime import wait_for

coding_session = session_fixture


def definition(data,address,sid,root,delivery):
    from copy import deepcopy

    result = deepcopy(data)
    result['sources'][0].update(root=str(root),include=['src/**'],poll_interval_ms=50)
    result['detectors'][0].update(debounce_ms=50,max_batch_delay_ms=100)
    result['handlers'][0].update(connection={'transport':'unix','path':str(address)},session=sid,delivery=delivery)
    return result


async def test_file_to_acp_coding_turn_then_self_edit_is_automatically_consumed(tmp_path,observer_data,coding_session):
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


async def test_offline_inbox_then_tui_review_and_owner_exit_leave_collection_alive(tmp_path,observer_data):
    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage
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
        return EventStream.empty(AssistantMessage())

    with tempfile.TemporaryDirectory(prefix='eyes-offline-') as short:
        ingress = await IngressServer.start(IngressConfig(manager._dir,store.root,Path(short)/'acp.sock'))
        runtime = ObserverRuntime(runtime_location(tmp_path))
        identifier = (await runtime.start(definition(observer_data,ingress.config.address,data.session_id,
                                                    tmp_path,'enqueue')))['registration_id']
        ui = None
        try:
            await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
            changed = tmp_path/'src'/'a.py'
            changed.write_text('offline edit')
            await wait_for(lambda:len(store.records(data.session_id,tmp_path))==1)
            assert store.records(data.session_id,tmp_path)[0].status=='pending_review'
            assert not turns and not manager.load_session(data.session_id).messages
            session = AgentSession(agent=Agent(model,config=AgentConfig(model='test')),
                session_data=manager.load_session(data.session_id),session_manager=manager,settings=SettingsManager(),
                working_dir=tmp_path,bash=None,tool_registry=None)
            ui = InteractiveMode(session)
            await asyncio.to_thread(ui._session_events.start)
            ui._replay_history()
            output = '\n'.join(strip_ansi(line) for line in ui._chat_log.render(120))
            assert 'pending_review' in output and 'Eyes' in output and 'a.py' in output
            assert not turns
            session.permissions.set_mode('allow-all')
            mid = store.records(data.session_id,tmp_path)[0].message.message_id
            await session.runtime.inbox.approve((mid,),peer_id='human-review')
            await wait_for(lambda:store.records(data.session_id,tmp_path)[0].status=='succeeded')
            assert len(turns)==1
            ui._poll()
            ui._request_exit()
            await asyncio.to_thread(ui._session_events.close)
            ui = None
            assert ingress.server.is_serving() and not runtime.entries[identifier].registration.task.done()
            changed.write_text('after TUI exit')
            await wait_for(lambda:len(store.records(data.session_id,tmp_path))==2)
            assert len(turns)==1
            assert any(row.status=='pending_review' for row in store.records(data.session_id,tmp_path))
            await runtime.stop_entry(identifier)
            assert len(store.records(data.session_id,tmp_path))==2 and ingress.server.is_serving()
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
