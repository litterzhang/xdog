"""Read-only inspection and explicitly selected owner review share one action path."""

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
    assert listed['messages'][0]['status']=='pending_review'
    shown = json.loads((await execute_command('inbox','show m1',coding_session)).output)
    assert shown['record']['message']['instruction']=='Review tests'
    assert shown['record']['message']['evidence']==[{'path':'a.py'}]
    assert shown['untrusted'] is True
    assert coding_session.runtime is None and not coding_session.messages


async def test_approve_without_a_runtime_does_not_start_another_owner(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command

    result = await inbox_command(coding_session,'approve m1')
    assert 'active session runtime' in result.output
    assert coding_session.runtime is None and not coding_session.messages


async def test_exact_command_review_uses_existing_permissions_and_controller(coding_session,pending):
    from xdog.coding.core.inbox_commands import inbox_command
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    try:
        denied = await inbox_command(coding_session,'approve m1')
        assert 'refused' in denied.output
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
        coding_session.permissions.set_mode('allow-all')
        receipt = json.loads((await inbox_command(coding_session,'approve m1')).output)
        assert receipt['status']=='accepted' and len(receipt['request_ids'])==1
        async with asyncio.timeout(3):
            while store.records(message.session_id,message.cwd)[0].status!='succeeded':
                await asyncio.sleep(0.01)
        assert runtime.controller.store.records()[0].input.id==receipt['request_ids'][0]
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_rpc_inbox_review_keeps_reader_available_for_permission_response(coding_session,pending,monkeypatch):
    import threading

    import xdog.coding.modes.rpc.rpc_mode as rpc

    store,message = pending
    permission,accepted,finished = threading.Event(),threading.Event(),threading.Event()
    request_id = ['']
    events = []

    class Output:
        def write(self,value):
            for line in value.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event.get('type')=='permission_request':
                    request_id[0] = event['id']
                    permission.set()
                elif event.get('type')=='inbox_result' and event.get('request_id')=='review':
                    if event.get('success'):
                        accepted.set()
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
                return json.dumps({'type':'inbox','action':'approve','message_ids':['m1'],
                                   'session_id':coding_session.session_id,'request_id':'review'})+'\n'
            if self.index==2:
                assert permission.wait(5)
                return json.dumps({'type':'permission_response','id':request_id[0],'decision':'allow_once'})+'\n'
            if self.index==3:
                assert accepted.wait(5)
                assert finished.wait(5)
                return '{"type":"quit"}\n'
            return ''

    monkeypatch.setattr(rpc.sys,'stdin',Input())
    monkeypatch.setattr(rpc.sys,'stdout',Output())
    assert await rpc.run_rpc_mode(coding_session)==0
    assert events[0]['type']=='ready'
    assert events[0]['capabilities']['session_inbox']==1
    result = next(e for e in events if e.get('type')=='inbox_result' and e.get('success'))
    assert result['result']['status']=='accepted'
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
    runtime.start()
    events = []
    helper = RpcInbox(coding_session,RpcNavigation(coding_session,events.append),events.append)
    packet = {'type':'inbox','session_id':coding_session.session_id,'request_id':'review',**change}
    try:
        with pytest.raises(ValueError):
            helper.start(packet)
        assert not helper.tasks and not coding_session.messages
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    finally:
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_rpc_review_cancel_is_not_an_execution_or_other_peer_abort(coding_session,pending):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.inbox import RpcInbox
    from xdog.coding.modes.rpc.navigation import RpcNavigation

    store,message = pending
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    events,requests = [],[]
    coding_session.permissions.set_request_handler(requests.append)
    helper = RpcInbox(coding_session,RpcNavigation(coding_session,events.append),events.append)
    try:
        helper.start({'type':'inbox','session_id':coding_session.session_id,'request_id':'review',
                      'action':'approve','message_ids':['m1']})
        async with asyncio.timeout(3):
            while not requests:
                await asyncio.sleep(0.01)
        helper.cancel({'type':'inbox_cancel','session_id':coding_session.session_id,'request_id':'review'})
        async with asyncio.timeout(3):
            while helper.tasks:
                await asyncio.sleep(0.01)
        assert any(e.get('cancelled') is True for e in events)
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
        assert not coding_session.messages and not runtime.controller.store.records()
    finally:
        await helper.aclose()
        await asyncio.wrap_future(runtime.close())
