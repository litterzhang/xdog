"""UI-046: prospective history/feedback before visible receipts; crash is never replay."""
from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, HandledInput, InputConsumedEvent, InputHandledEvent
from xdog.agent.supervision import ChildSpec, ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('handled', [False, True])
@pytest.mark.parametrize('failure', [False, True])
async def test_checkpoint_precedes_public_receipt_and_failure_refuses_admission(handled, failure):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, events = [], []
    async def transform(message, source, cancel):
        return HandledInput('HANDLED FEEDBACK') if handled else message
    async def checkpoint(event):
        if entered.is_set():
            return
        assert not any(isinstance(e, (InputConsumedEvent, InputHandledEvent)) for e in events)
        assert not agent.messages and not calls
        entered.set()
        await release.wait()
        if failure:
            raise OSError('Owned admission checkpoint refused')
    agent = Agent(lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
                  config=AgentConfig(model='offline/model'), input_transform=transform)
    agent.set_input_checkpoint(checkpoint)
    agent.subscribe(events.append)
    agent.queue_input('original-id', 'ORIGINAL INPUT', 'steer')
    stream = await agent.prompt('INITIAL')
    work = asyncio.create_task(stream.collect())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not agent.messages and not calls
        release.set()
        await asyncio.wait_for(work, 3)
        if failure:
            assert not any(isinstance(e, (InputConsumedEvent, InputHandledEvent)) for e in events)
            assert not any(isinstance(m, UserMessage) for m in agent.messages)
            assert 'checkpoint refused' in agent.state.error and not calls
            assert agent.pending_inputs()[0].content == 'ORIGINAL INPUT'
        elif handled:
            assert not calls and not agent.messages
            assert len([e for e in events if isinstance(e, InputHandledEvent)]) == 2
        else:
            assert calls and any(isinstance(e, InputConsumedEvent) for e in events)
    finally:
        release.set()
        await agent.cancel_and_wait()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.skipif(sys.platform != 'linux', reason='Owned current Linux SIGKILL/fsync boundary proof')
@pytest.mark.parametrize('handled', [False, True])
@pytest.mark.parametrize('boundary', ['before-checkpoint', 'before-replace', 'after-replace', 'after-checkpoint',
                                      'before-settle', 'after-settle'])
def test_owned_sigkill_history_feedback_and_queue_boundary_never_replays(tmp_path, handled, boundary):
    script = tmp_path / 'owned_checkpoint.py'
    script.write_text('''import asyncio,json,sys,threading,os
from pathlib import Path
from xdog.agent import Agent,AgentConfig,HandledInput
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage,DoneEvent,TextContent,UserMessage,ImageContent
from xdog.ai.utils.event_stream import EventStream
PNG="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="
async def main():
    store=ChildStore(Path(sys.argv[1])); boundary=sys.argv[2]; handled=sys.argv[3]=='True'
    release=threading.Event(); calls=[]; child_id=[]; armed=[False]
    def stop(name):
        if name==boundary:
            print(json.dumps({'id':child_id[0],'boundary':name}),flush=True)
            threading.Event().wait(30)
            raise RuntimeError('Owned test checkpoint was not killed')
    replace=os.replace
    def replace_checkpoint(source,destination):
        target=armed[0] and str(destination).endswith('.admission.json')
        if target: stop('before-replace')
        replace(source,destination)
        if target: stop('after-replace')
    os.replace=replace_checkpoint
    def admission(record):
        target=('CHECKPOINT FEEDBACK' in record.get('input_feedback',[]) if handled else
                any('TRANSFORMED TARGET' in json.dumps(row.get('content')) for row in record['messages']))
        if target: stop('before-checkpoint')
        armed[0]=target
        store.save_admission(record)
        armed[0]=False
        if target: stop('after-checkpoint')
    def inputs(identifier,rows):
        if not rows:
            stop('before-settle')
        store.save_inputs(identifier,rows)
        if not rows:
            stop('after-settle')
    def factory(spec,publish):
        async def transform(message,source,cancel):
            if source=='prompt': return message
            return (HandledInput('CHECKPOINT FEEDBACK') if handled else
                    UserMessage(content=(TextContent(text='TRANSFORMED TARGET'), *message.content[1:])))
        def stream(*args):
            calls.append(True)
            async def events():
                if len(calls)==1:
                    while not release.is_set(): await asyncio.sleep(.003)
                else:
                    await asyncio.sleep(30)
                yield DoneEvent(message=AssistantMessage(content=(TextContent(text='first'),)))
            future=asyncio.get_running_loop().create_future()
            future.set_result(AssistantMessage(content=(TextContent(text='first'),)))
            return EventStream.from_async_generator(events(),result_future=future)
        return Agent(stream,config=AgentConfig(model='offline/model'),input_transform=transform)
    owner=ChildSupervisor(factory,store.save,begin=store.begin,persist_inputs=inputs,
                          persist_admission=admission)
    child=await owner.start('INITIAL');child_id.append(child)
    while not calls: await asyncio.sleep(.003)
    await owner.control('follow-up',child,'ORIGINAL TARGET',
                        images=(ImageContent(data=PNG,mime_type='image/png'),))
    release.set()
    await asyncio.sleep(30)
asyncio.run(main())
''')
    root = tmp_path / 'records'
    process = subprocess.Popen([sys.executable, '-I', str(script), str(root), boundary, str(handled)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 8)
        assert ready, 'Owned boundary receipt absent before deadline'
        # Byte reads prevent a partially written line from becoming an unbounded readline.
        receipt = bytearray()
        import time
        deadline = time.monotonic() + 2
        while not receipt.endswith(b'\n'):
            assert time.monotonic() < deadline, 'Partial boundary receipt'
            assert select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]
            byte = os.read(process.stdout.fileno(), 1)
            assert byte
            receipt.extend(byte)
        result = json.loads(receipt)
        assert result['boundary'] == boundary
        process.kill()
        assert process.wait(timeout=5) == -9
        records = ChildStore(root).load()
        record = records[0]
        assert record['status'] == 'interrupted' and record['queue_uncertain'] is True
        committed = boundary not in {'before-checkpoint', 'before-replace'}
        if handled:
            assert ('CHECKPOINT FEEDBACK' in record.get('input_feedback', [])) == committed
            assert 'TRANSFORMED TARGET' not in str(record['messages'])
        else:
            admitted = any('TRANSFORMED TARGET' in json.dumps(row.get('content')) for row in record['messages'])
            assert admitted == committed
        pending = record['undelivered_inputs']
        assert [row['content'] for row in pending] == ([] if boundary == 'after-settle' else ['ORIGINAL TARGET'])
        if pending:
            assert pending[0]['images'][0]['mime_type'] == 'image/png'
            assert pending[0]['images'][0]['data'].startswith('iVBORw0KGgo')
        if committed and not handled:
            assert any('iVBORw0KGgo' in json.dumps(row['content']) for row in record['messages'])
        async def inspect():
            owner = ChildSupervisor(lambda *args: pytest.fail('Crash recovery must not restart/model replay'),
                                    records=records)
            try:
                assert not owner.active
                queue = (await owner.control('queue', result['id'], '{"action":"list"}'))['queue']
                assert queue['stopped'] is True
                assert all(row['uncertain'] for row in queue['inputs'])
                assert (await owner.control('inspect', result['id']))['messages'] == record['messages']
            finally:
                await owner.aclose()
        asyncio.run(inspect())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('status', ['completed', 'interrupted', 'canceled'])
def test_terminal_record_supersedes_admission_even_when_cleanup_interrupted(tmp_path, monkeypatch, status):
    store = ChildStore(tmp_path / 'records')
    spec = ChildSpec('0123456789ab', 'TASK')
    store.begin(spec)
    checkpoint = store.load()[0]
    checkpoint.update(messages=[{'role': 'user', 'content': 'ADMITTED'}], settling=False)
    store.save_admission(checkpoint)
    original = Path.unlink
    def fail(path, *args, **kwargs):
        if '.admission.' in path.name:
            raise OSError('Owned cleanup interruption')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(OSError, match='cleanup interruption'):
        store.save({**checkpoint, 'status': status, 'messages': [{'role': 'user', 'content': 'FINAL'}]})
    assert store._path(spec.id, '.admission').exists()
    assert store.load()[0]['status'] == status
    assert store.load()[0]['messages'][0]['content'] == 'FINAL'


@pytest.mark.parametrize('fault', ['id', 'task', 'status', 'settling', 'messages'])
def test_admission_checkpoint_rejects_foreign_or_invalid_saved_records(tmp_path, fault):
    store = ChildStore(tmp_path / 'records')
    spec = ChildSpec('0123456789ab', 'TASK')
    store.begin(spec)
    record = store.load()[0]
    record[fault] = {'id': 'abcdef012345', 'task': 'FOREIGN', 'status': 'running',
                     'settling': True, 'messages': 'invalid'}[fault]
    store._write(store._path(spec.id, '.admission'), record)
    with pytest.raises(ValueError):
        store.load()


@pytest.mark.parametrize('action', ['cancel', 'close'])
@pytest.mark.parametrize('written', [False, True])
async def test_cancel_close_reachable_while_checkpoint_writer_owns_storage(tmp_path, action, written):
    import threading
    entered, release = threading.Event(), threading.Event()
    store = ChildStore(tmp_path / 'records')
    contexts = []
    def checkpoint(record):
        if written:
            store.save_admission(record)
        entered.set()
        assert release.wait(5)
        if not written:
            store.save_admission(record)
    owner = ChildSupervisor(lambda *args: Agent(
        lambda *args: contexts.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model='offline/model')), store.save, begin=store.begin,
        persist_inputs=store.save_inputs, persist_admission=checkpoint, forget=store.forget)
    cancel = None
    try:
        child = await owner.start('INITIAL')
        assert await asyncio.to_thread(entered.wait, 3)
        cancel = asyncio.create_task(owner.control(action, child))
        await asyncio.sleep(0.05)
        assert not cancel.done() and owner._children[child].queue_work
        assert not contexts  # No provider entry before admission; never restart after cancel.
        release.set()
        result = await asyncio.wait_for(cancel, 3)
        assert result['status'] == 'canceled'
        assert not owner.active and not contexts
        if action == 'close':
            assert store.load() == []
        else:
            assert store.load()[0]['status'] == 'canceled'
            assert store.load()[0]['queue_uncertain'] is True
            assert store.load()[0]['initial_state'] == 'uncertain'
            queue = (await owner.control('queue', child, '{"action":"list"}'))['queue']
            assert queue['inputs'][-1]['uncertain'] is True
        assert not store._path(child, '.admission').exists()
    finally:
        release.set()
        if cancel is not None:
            await asyncio.gather(cancel, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize('failure', ['replace', 'directory-fsync'])
def test_failed_admission_replace_or_fsync_exposes_only_valid_old_or_new_checkpoint(tmp_path, monkeypatch, failure):
    store = ChildStore(tmp_path / 'records')
    spec = ChildSpec('0123456789ab', 'TASK')
    store.begin(spec)
    record = store.load()[0]
    old = {**record, 'messages': [{'role': 'user', 'content': 'OLD'}]}
    store.save_admission(old)
    new = {**record, 'messages': [{'role': 'user', 'content': 'NEW'}]}
    def fail(*args):
        raise OSError('Owned checkpoint storage failure')
    if failure == 'replace':
        monkeypatch.setattr(os, 'replace', fail)
    else:
        monkeypatch.setattr(store, '_sync_directory', fail)
    with pytest.raises(OSError, match='storage failure'):
        store.save_admission(new)
    loaded = store.load()[0]
    assert loaded['messages'][0]['content'] == ('OLD' if failure == 'replace' else 'NEW')
    assert loaded['status'] == 'interrupted'
    assert not list(store.root.glob('.child-*'))
