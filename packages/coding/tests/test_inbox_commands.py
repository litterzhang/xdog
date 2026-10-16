"""Automatic delivery and read-only inspection preserve owner and tool-permission boundaries."""

import asyncio
import json

import pytest


@pytest.fixture
def pending(coding_session,tmp_path):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    message = InboxMessage(coding_session.session_id,tmp_path,'m1','Review tests',({'path':'a.py'},),'Eyes',{})
    store.enqueue(message)
    return store,message


@pytest.mark.parametrize('args',['approve','approve --all','retry','approve m1 m1','show','unknown','show a b'])
async def test_inbox_commands_never_implicitly_select_a_batch(coding_session,args):
    from xdog.coding.core.inbox_commands import inbox_command

    result = await inbox_command(coding_session,args)
    assert result.output.startswith('Usage: /inbox')
    assert coding_session.messages==[] and coding_session.runtime is None


async def test_list_and_show_keep_the_session_model_free_and_do_not_create_a_runtime(coding_session,pending):
    from xdog.coding.core.slash_commands import execute_command

    store,message = pending
    listed = json.loads((await execute_command('inbox','',coding_session)).output)
    assert listed['messages'][0]['message_id']=='m1'
    assert listed['messages'][0]['status']=='pending'
    shown = json.loads((await execute_command('inbox','show m1',coding_session)).output)
    assert shown['record']['message']['instruction']=='Review tests'
    assert shown['record']['message']['evidence']==[{'path':'a.py'}]
    assert shown['untrusted'] is True
    assert coding_session.runtime is None and not coding_session.messages


async def test_approve_without_a_runtime_does_not_start_another_owner(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command

    result = await inbox_command(coding_session,'approve m1')
    assert result.output.startswith('Usage: /inbox') and 'obsolete' in result.output
    assert coding_session.runtime is None and not coding_session.messages


async def test_obsolete_command_never_grants_execution_and_delivery_is_automatic(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    requests = []
    coding_session.permissions.set_request_handler(requests.append)
    try:
        for mode in ('ask','allow-all'):
            coding_session.permissions.set_mode(mode)
            result = await inbox_command(coding_session,'approve m1')
            assert result.output.startswith('Usage: /inbox') and 'obsolete' in result.output
            assert store.records(message.session_id,message.cwd)[0].status=='pending'
        coding_session.permissions.set_mode('ask')
        await runtime.activate_inputs()
        async with asyncio.timeout(3):
            while store.records(message.session_id,message.cwd)[0].status!='succeeded':
                await asyncio.sleep(0.01)
        row = store.records(message.session_id,message.cwd)[0]
        assert runtime.controller.store.records()[0].input.id==row.request_id
        assert not requests and not coding_session.permissions.has_pending_requests
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_native_inspection_remains_available_during_actual_tool_permission(coding_session,pending):
    from xdog.agent import AgentTool, AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    permission = asyncio.Event()
    requests,calls,effects = [],[],[]

    async def execute(*args,**kwargs):
        effects.append(True)
        return AgentToolResult(content=(TextContent(text='written'),))

    def request(value):
        requests.append(value)
        permission.set()

    def stream(*args):
        calls.append(True)
        content = ((ToolCall(id='write',name='batch_write',arguments={}),) if len(calls)==1
                   else (TextContent(text='finished'),))
        return EventStream.empty(AssistantMessage(content=content))

    coding_session.agent.set_tools([AgentTool(name='batch_write',description='write',parameters={},execute=execute)])
    coding_session.agent.set_stream_fn(stream)
    coding_session.permissions.set_mode('ask')
    coding_session.permissions.set_request_handler(request)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        await asyncio.wait_for(permission.wait(),3)
        assert requests[0].tool_name=='batch_write' and not effects
        listed = await asyncio.wait_for(inbox_action(coding_session,'list'),1)
        assert listed['messages'][0]['status']=='running'
        assert coding_session.permissions.resolve(requests[0].id,'allow_once')
        async with asyncio.timeout(3):
            while store.records(message.session_id,message.cwd)[0].status!='succeeded':
                await asyncio.sleep(0.01)
        assert effects==[True] and len(calls)==2
        assert not coding_session.permissions.has_pending_requests
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_opaque_ids_are_quoted_data_not_shell_commands(coding_session,pending,tmp_path):
    import shlex
    from dataclasses import replace

    from xdog.coding.core.inbox_commands import inbox_command

    store,message = pending
    opaque = 'id with spaces;$(touch escaped)'
    store.enqueue(replace(message,message_id=opaque))
    result = json.loads((await inbox_command(coding_session,'show '+shlex.quote(opaque))).output)
    assert result['record']['message']['message_id']==opaque
    assert not (tmp_path/'escaped').exists()


@pytest.mark.parametrize('action,ids,cursor',[
    ('approve',('m1',),0), ('approve',('m1','m1'),0), ('list',(),True),
    ('list',('m1',),0), ('show',('m1','m1'),0), ('cancel',(),0),
    ('retry',('--all',),0), ('show',('m1',),1),
])
async def test_native_inbox_rejects_invalid_controls_before_work(coding_session,pending,action,ids,cursor):
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        with pytest.raises(ValueError):
            await inbox_action(coding_session,action,ids,cursor=cursor)
        assert not coding_session.messages
        assert store.records(message.session_id,message.cwd)[0].status=='pending'
        assert not runtime.controller.store.records()
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_native_inspection_cancel_does_not_abort_another_peer_execution(coding_session,pending,monkeypatch):
    from xdog.ai.types import AssistantMessage, DoneEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    import threading

    started,release = asyncio.Event(),asyncio.Event()
    inspecting,inspection_release,inspection_settled = threading.Event(),threading.Event(),threading.Event()
    requests = []

    def stream(*args):
        result = asyncio.get_running_loop().create_future()
        result.set_result(AssistantMessage())
        async def generate():
            started.set()
            await release.wait()
            yield DoneEvent(message=AssistantMessage())
        return EventStream(generate(),result)

    original = store.records
    def slow_inspection(*args,**kwargs):
        inspecting.set()
        try:
            assert inspection_release.wait(3)
            return original(*args,**kwargs)
        finally:
            inspection_settled.set()

    coding_session.agent.set_stream_fn(stream)
    coding_session.permissions.set_request_handler(requests.append)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    inspection = None
    try:
        await asyncio.wait_for(started.wait(),2)
        with monkeypatch.context() as scoped:
            scoped.setattr(store,'records',slow_inspection)
            inspection = asyncio.create_task(inbox_action(coding_session,'list'))
            assert await asyncio.to_thread(inspecting.wait,2)
            inspection.cancel()
            with pytest.raises(asyncio.CancelledError):
                await inspection
        inspection_release.set()
        assert await asyncio.to_thread(inspection_settled.wait,2)
        assert store.records(message.session_id,message.cwd)[0].status=='running'
        assert coding_session.agent.is_streaming and not requests
        release.set()
        async with asyncio.timeout(2):
            while store.records(message.session_id,message.cwd)[0].status!='succeeded':
                await asyncio.sleep(0.01)
    finally:
        release.set()
        inspection_release.set()
        if inspection is not None:
            await asyncio.gather(inspection,return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_native_cancel_targets_pending_message_without_requesting_permission(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    requests = []
    coding_session.permissions.set_request_handler(requests.append)
    try:
        result = await inbox_action(coding_session,'cancel',('m1',))
        assert result['status']=='cancelled'
        assert store.records(message.session_id,message.cwd)[0].status=='cancelled'
        assert not requests and not coding_session.messages and not runtime.controller.store.records()
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_obsolete_approval_reports_automatic_delivery_without_starting_owner(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command
    result = await inbox_command(coding_session,'approve m1')
    assert result.output.startswith('Usage: /inbox')
    assert 'obsolete' in result.output and 'automatic' in result.output
    assert coding_session.runtime is None and not coding_session.permissions.has_pending_requests


@pytest.mark.parametrize('change',['session','branch'])
async def test_native_inbox_does_not_deliver_read_results_after_owner_context_changes(
    coding_session,pending,monkeypatch,change,
):
    import threading

    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,_ = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    entered,release = threading.Event(),threading.Event()
    records = store.records
    def held(*args,**kwargs):
        result = records(*args,**kwargs)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(store,'records',held)
    original_id = coding_session.session_data.session_id
    original_branch = coding_session.session_data.active_branch
    task = asyncio.create_task(inbox_action(coding_session,'list'))
    try:
        assert await asyncio.to_thread(entered.wait,2)
        if change=='session':
            coding_session.session_data.session_id='replacement-session'
        else:
            coding_session.session_data.active_branch='replacement-branch'
        release.set()
        with pytest.raises(RuntimeError,match='context changed'):
            await task
        assert not coding_session.messages
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)
        coding_session.session_data.session_id = original_id
        coding_session.session_data.active_branch = original_branch
        await asyncio.wrap_future(runtime.close())
