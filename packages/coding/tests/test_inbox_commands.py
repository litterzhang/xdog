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


async def test_rpc_inspection_keeps_reader_available_for_actual_tool_permission(coding_session,pending,monkeypatch):
    import threading

    import xdog.coding.modes.rpc.rpc_mode as rpc
    from xdog.agent import AgentTool, AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    store,message = pending
    permission,listed,finished = threading.Event(),threading.Event(),threading.Event()
    request_id,calls,effects,events = [''],[],[],[]

    async def execute(*args,**kwargs):
        effects.append(True)
        return AgentToolResult(content=(TextContent(text='written'),))

    coding_session.agent.set_tools([AgentTool(name='batch_write',description='write',parameters={},execute=execute)])

    def stream(*args):
        calls.append(True)
        content = ((ToolCall(id='write',name='batch_write',arguments={}),) if len(calls)==1
                   else (TextContent(text='finished'),))
        return EventStream.empty(AssistantMessage(content=content))
    coding_session.agent.set_stream_fn(stream)

    class Output:
        def write(self,value):
            for line in value.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event.get('type')=='permission_request':
                    assert event['tool']=='batch_write' and not effects
                    request_id[0] = event['id']
                    permission.set()
                elif event.get('type')=='inbox_result' and event.get('success'):
                    listed.set()
                elif event.get('type')=='inbox_snapshot':
                    if any(r['status']=='succeeded' for r in event.get('messages',[])):
                        finished.set()
            return len(value)
        def flush(self):
            pass

    class Input:
        index = 0
        def readline(self,size=-1):
            self.index+=1
            if self.index==1:
                return json.dumps({'type':'inbox','action':'list','session_id':coding_session.session_id,
                                   'request_id':'inspect'})+'\n'
            if self.index==2:
                assert permission.wait(5)
                return json.dumps({'type':'permission_response','id':request_id[0],'decision':'allow_once'})+'\n'
            if self.index==3:
                assert listed.wait(5) and finished.wait(5)
                return '{"type":"quit"}\n'
            return ''

    monkeypatch.setattr(rpc.sys,'stdin',Input())
    monkeypatch.setattr(rpc.sys,'stdout',Output())
    assert await rpc.run_rpc_mode(coding_session)==0
    assert events[0]['type']=='ready' and events[0]['capabilities']['session_inbox']==1
    assert effects==[True] and len(calls)==2
    assert store.records(message.session_id,message.cwd)[0].status=='succeeded'


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


@pytest.mark.parametrize('change',[
    {'session_id':'other'}, {'generation':'old'}, {'origin':'human'}, {'permission':'allow-all'},
    {'action':'approve','message_ids':'m1'}, {'action':'approve','message_ids':['m1','m1']},
    {'action':'list','cursor':True}, {'action':'list','message_ids':['m1']},
])
async def test_rpc_inbox_rejects_stale_or_privilege_fields_before_work(coding_session,pending,change):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.inbox import RpcInbox
    from xdog.coding.modes.rpc.navigation import RpcNavigation

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    events = []
    helper = RpcInbox(coding_session,RpcNavigation(coding_session,events.append),events.append)
    packet = {'type':'inbox','session_id':coding_session.session_id,'request_id':'review',**change}
    try:
        with pytest.raises(ValueError):
            helper.start(packet)
        assert not helper.tasks and not coding_session.messages
        assert store.records(message.session_id,message.cwd)[0].status=='pending'
    finally:
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_rpc_inspection_cancel_does_not_abort_another_peer_execution(coding_session,pending,monkeypatch):
    import xdog.coding.modes.rpc.inbox as inbox_module
    from xdog.ai.types import AssistantMessage, DoneEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.inbox import RpcInbox
    from xdog.coding.modes.rpc.navigation import RpcNavigation

    store,message = pending
    started,release,inspecting = asyncio.Event(),asyncio.Event(),asyncio.Event()
    events,requests = [],[]

    def stream(*args):
        result = asyncio.get_running_loop().create_future()
        result.set_result(AssistantMessage())
        async def generate():
            started.set()
            await release.wait()
            yield DoneEvent(message=AssistantMessage())
        return EventStream(generate(),result)

    async def slow_inspection(*args,**kwargs):
        inspecting.set()
        await asyncio.Event().wait()

    coding_session.agent.set_stream_fn(stream)
    coding_session.permissions.set_request_handler(requests.append)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    helper = RpcInbox(coding_session,RpcNavigation(coding_session,events.append),events.append)
    monkeypatch.setattr(inbox_module,'inbox_action',slow_inspection)
    try:
        await asyncio.wait_for(started.wait(),2)
        helper.start({'type':'inbox','session_id':coding_session.session_id,'request_id':'inspect','action':'list'})
        await asyncio.wait_for(inspecting.wait(),2)
        helper.cancel({'type':'inbox_cancel','session_id':coding_session.session_id,'request_id':'inspect'})
        async with asyncio.timeout(2):
            while helper.tasks:
                await asyncio.sleep(0.01)
        assert any(e.get('cancelled') is True for e in events)
        assert store.records(message.session_id,message.cwd)[0].status=='running'
        assert coding_session.agent.is_streaming and not requests
        release.set()
        async with asyncio.timeout(2):
            while store.records(message.session_id,message.cwd)[0].status!='succeeded':
                await asyncio.sleep(0.01)
    finally:
        release.set()
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_rpc_cancel_targets_pending_message_without_requesting_permission(coding_session,pending):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.inbox import RpcInbox
    from xdog.coding.modes.rpc.navigation import RpcNavigation

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    events,requests = [],[]
    coding_session.permissions.set_request_handler(requests.append)
    helper = RpcInbox(coding_session,RpcNavigation(coding_session,events.append),events.append)
    try:
        helper.start({'type':'inbox','session_id':coding_session.session_id,'request_id':'cancel',
                      'action':'cancel','message_id':'m1'})
        async with asyncio.timeout(2):
            while helper.tasks:
                await asyncio.sleep(0.01)
        result, = events
        assert result['success'] and result['result']['status']=='cancelled'
        assert store.records(message.session_id,message.cwd)[0].status=='cancelled'
        assert not requests and not coding_session.messages and not runtime.controller.store.records()
    finally:
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_obsolete_approval_reports_automatic_delivery_without_starting_owner(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command
    result = await inbox_command(coding_session,'approve m1')
    assert result.output.startswith('Usage: /inbox')
    assert 'obsolete' in result.output and 'automatic' in result.output
    assert coding_session.runtime is None and not coding_session.permissions.has_pending_requests


@pytest.mark.parametrize('change',['generation','revision'])
async def test_rpc_inbox_does_not_deliver_read_results_after_owner_context_changes(
    coding_session,pending,monkeypatch,change,
):
    import xdog.coding.modes.rpc.inbox as module
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.inbox import RpcInbox
    from xdog.coding.modes.rpc.navigation import RpcNavigation

    store,_ = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    events = []
    navigation = RpcNavigation(coding_session,events.append)
    helper = RpcInbox(coding_session,navigation,events.append)
    entered,release = asyncio.Event(),asyncio.Event()
    action = module.inbox_action

    async def held(*args,**kwargs):
        result = await action(*args,**kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(module,'inbox_action',held)
    try:
        helper.start({'type':'inbox','session_id':coding_session.session_id,'request_id':'held','action':'list'})
        await asyncio.wait_for(entered.wait(),2)
        if change=='generation':
            await asyncio.wrap_future(runtime.controller.rotate(runtime.controller.branch))
        else:
            navigation.refresh_revision()
        release.set()
        async with asyncio.timeout(2):
            while helper.tasks:
                await asyncio.sleep(0.01)
        result, = events
        assert not result['success'] and 'context changed' in result['message'].lower()
        assert 'result' not in result and not coding_session.messages
    finally:
        release.set()
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())
