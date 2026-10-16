"""Durable ACP receipts are not agent completion or permission to execute."""

import asyncio
from dataclasses import replace

import pytest
from xdog.eyes.deliveries import Delivery
from xdog.eyes.executor import observe
from xdog.eyes.models import Event, FileEvidence, FileState

from packages.eyes.tests.test_acp_handler import definition, delivery, rows


def enqueue_definition(record,mode='inbox',gate=None):
    return replace(definition(record,mode=mode,gate=gate),delivery='enqueue')


async def test_enqueue_does_not_load_or_prompt_an_inactive_owner(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(enqueue_definition(record),cwd=tmp_path)
    try:
        await handler.start()
        assert handler.owner_generation is None
        for identifier in ('stable-message','second-message'):
            result = await handler.deliver(delivery(identifier))
            assert result.status=='queued'
            assert result.receipt_status=='queued' and result.message_state=='pending_review'
        assert not await handler.resume()  # Nothing paused; no model resume is ever requested.
        log = rows(record)
        assert not [r for r in log if r['kind'] in {'load','prompt'}]
        sent = [r['params'] for r in log if r['kind']=='extension' and r['method']=='xdog/enqueue']
        assert [r['message_id'] for r in sent]==['stable-message','second-message']
        assert set(sent[0])=={'session_id','cwd','message_id','instruction','evidence','source','provenance'}
        assert sent[0]['evidence'][0]['type']=='file.created'
        assert sent[0]['evidence'][0]['evidence']==[{
            'path':'src/a.py; $(touch should-not-exist)','before':None,
            'after':{'digest':'hash','size':3},'observed_at':1.0,'root':'/project'}]
        assert sent[0]['provenance']['delivery_id']=='stable-message'
        assert sent[0]['source']=='Eyes'
        assert handler.snapshot().state == 'ready'
    finally:
        await handler.close()


async def test_duplicate_receipt_preserves_existing_message_state(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    handler = AcpHandler(enqueue_definition(tmp_path/'peer.jsonl'),cwd=tmp_path)
    try:
        await handler.start()
        assert (await handler.deliver(delivery('same'))).receipt_status=='queued'
        duplicate = await handler.deliver(delivery('same'))
        assert duplicate.status=='queued' and duplicate.receipt_status=='duplicate'
        assert duplicate.message_state=='pending_review'
    finally:
        await handler.close()


@pytest.mark.parametrize('mode',['success','inbox-bool-version','inbox-wrong-scope','inbox-wrong-cwd','inbox-no-store'])
async def test_missing_extension_or_invalid_target_scope_fails_before_intake(tmp_path,mode):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(enqueue_definition(record,mode=mode),cwd=tmp_path)
    try:
        with pytest.raises(ValueError):
            await handler.start()
        assert not [r for r in rows(record) if r['kind'] in {'load','prompt'}]
        assert not [r for r in rows(record) if r['kind']=='extension' and r['method']=='xdog/enqueue']
    finally:
        await handler.close()


@pytest.mark.parametrize('mode',['inbox-bad-id','inbox-bad-session','inbox-bad-state','inbox-bad-branch'])
async def test_malformed_ack_is_unknown_and_never_model_success(tmp_path,mode):
    from xdog.eyes.handlers.acp import AcpHandler

    handler = AcpHandler(enqueue_definition(tmp_path/'peer.jsonl',mode=mode),cwd=tmp_path)
    try:
        await handler.start()
        result = await handler.deliver(delivery())
        assert result.status=='outcome-unknown' and result.retry_on_resume
        assert (await handler.deliver(delivery('must-not-pass'))).status=='outcome-unknown'
    finally:
        await handler.close()


async def test_ack_loss_requires_explicit_same_store_reconnect_and_same_message_id(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(enqueue_definition(record,mode='inbox-disconnect-once'),cwd=tmp_path)
    try:
        await handler.start()
        result = await handler.deliver(delivery('stable'))
        assert result.status=='outcome-unknown' and result.retry_on_resume
        assert (await handler.deliver(delivery('later'))).status=='outcome-unknown'
        assert len([r for r in rows(record) if r['kind']=='initialize'])==1
        assert await handler.resume()  # Explicit data-only reconnect, not execution approval.
        wrong = await handler.deliver(delivery('different'))
        assert wrong.status=='paused'
        recovered = await handler.deliver(delivery('stable'))
        assert recovered.status=='queued' and recovered.receipt_status=='duplicate'
        assert len([r for r in rows(record) if r['kind']=='stored'])==1
        assert not [r for r in rows(record) if r['kind'] in {'load','prompt'}]
        assert not [r for r in rows(record) if r.get('method')=='xdog/resume']
    finally:
        await handler.close()


@pytest.mark.parametrize('mode',['inbox-changed-store','inbox-changed-branch'])
async def test_resume_cannot_silently_move_to_another_store_or_branch(tmp_path,mode):
    from xdog.eyes.handlers.acp import AcpHandler

    record,gate = tmp_path/'peer.jsonl',tmp_path/'changed'
    handler = AcpHandler(enqueue_definition(record,mode=mode,gate=gate),cwd=tmp_path)
    try:
        await handler.start()
        gate.touch()
        result = await handler.deliver(delivery())
        assert result.status in {'paused','cancelled'}
        assert not await handler.resume()
        assert not [r for r in rows(record) if r.get('method')=='xdog/enqueue']
    finally:
        await handler.close()


async def test_capacity_refusal_pauses_without_any_prompt_or_authorization(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(enqueue_definition(record,mode='inbox-quota'),cwd=tmp_path)
    try:
        await handler.start()
        assert (await handler.deliver(delivery())).status=='paused'
        assert not [r for r in rows(record) if r['kind'] in {'load','prompt'}]
        assert not [r for r in rows(record) if r.get('method')=='xdog/resume']
    finally:
        await handler.close()


async def test_queued_receipts_keep_collecting_and_never_emit_model_completion(tmp_path,observer_data):
    from xdog.eyes.events import DeliveryResult
    from xdog.eyes.loader import parse
    from xdog.eyes.sources.file import FileSnapshot
    from xdog.eyes.testing import FakeClock

    observer_data['handlers'][0]['delivery']='enqueue'
    definition = parse(observer_data,base_dir=tmp_path)
    clock,cancel = FakeClock(limit=8),asyncio.Event()
    events,sent,scans = [],[],[]

    async def scan(source):
        scans.append(clock.now())
        return FileSnapshot(()) if len(scans)==1 else FileSnapshot((('a',FileState(str(len(scans)),3)),))

    async def send(item):
        sent.append(item)
        return DeliveryResult('queued',receipt_status='duplicate' if len(sent)==2 else 'queued',
                              message_state='pending_review')

    def emit(event):
        events.append(event)
        if sum(e.kind=='delivery_queued' for e in events)==2:
            cancel.set()

    await observe(definition,registration_id='r',scanner=scan,deliver=send,emit=emit,
                  clock=clock.now,sleep=clock.sleep,cancel=cancel,cwd=tmp_path)
    assert len(sent)==2 and len(scans)>=3
    assert not any(e.kind=='delivery_succeeded' for e in events)
    assert [e.receipt_status for e in events if e.kind=='delivery_queued']==['queued','duplicate']


def test_enqueue_size_is_not_limited_by_duplicated_prompt_evidence(tmp_path,observer_data):
    from xdog.eyes import executor
    from xdog.eyes.loader import parse

    observer_data['handlers'][0]['delivery']='enqueue'
    config = parse(observer_data,base_dir=tmp_path)
    relative = '/'.join('x'*100 for _ in range(29))
    evidence = tuple(FileEvidence(f'{i}/{relative}',None,FileState('h',3),1.0,'/project') for i in range(200))
    item = Delivery('d','r','review',(Event('e','files','changes','file.created',1.0,evidence),))
    from xdog.eyes.handlers.acp import AcpHandler

    assert executor._bounded(item)
    AcpHandler(config.handlers[0], cwd=tmp_path).validate(item)
    with pytest.raises(ValueError, match='capacity'):
        AcpHandler(replace(config.handlers[0], delivery='prompt'), cwd=tmp_path).validate(item)


@pytest.mark.parametrize('replace_store',[False,True])
async def test_real_unix_intake_survives_service_restart_only_for_the_same_store(tmp_path,monkeypatch,replace_store):
    import tempfile
    from pathlib import Path

    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.eyes import acp_client
    from xdog.eyes.connections import UnixConnection
    from xdog.eyes.handlers.acp import AcpHandler
    from xdog.eyes.models import AcpHandlerDef

    def forbidden(*args,**kwargs):
        raise AssertionError('Unix durable intake cannot spawn CLI or construct an AgentSession')

    monkeypatch.setattr(acp_client,'spawn_agent_process',forbidden)
    monkeypatch.setattr(AgentSession,'__post_init__',forbidden)
    manager = SessionManager(tmp_path/'sessions')
    data = manager.create_session(working_dir=tmp_path)
    with tempfile.TemporaryDirectory(prefix='eyes-durable-') as short:
        config = IngressConfig(manager._dir,tmp_path/'inbox',Path(short)/'acp.sock')
        server = await IngressServer.start(config)
        handler = AcpHandler(AcpHandlerDef('h','acp',UnixConnection(config.address),data.session_id,'Review','enqueue'),
                             cwd=tmp_path)
        try:
            await handler.start()
            namespace = handler.client.inbox_id
            assert (await handler.deliver(delivery('first'))).status=='queued'
            assert handler.process is None and handler.owner_generation is None
            await server.aclose()
            replacement = replace(config,inbox_dir=tmp_path/'different') if replace_store else config
            server = await IngressServer.start(replacement)
            assert (await handler.deliver(delivery('second'))).status=='paused'
            resumed = await handler.resume()
            assert resumed is not replace_store
            if resumed:
                assert handler.client.inbox_id==namespace
                assert (await handler.deliver(delivery('second'))).status=='queued'
                assert len(list(config.inbox_dir.rglob('*.json')))==2
            else:
                assert not list(replacement.inbox_dir.rglob('*.json'))
            assert not manager.load_session(data.session_id).messages
        finally:
            await handler.close()
            await server.aclose()


async def test_close_settles_an_explicit_reconnect_that_is_still_initializing(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record,gate = tmp_path/'peer.jsonl',tmp_path/'initialize'
    handler = AcpHandler(enqueue_definition(record,mode='inbox-disconnect-once-slow',gate=gate),cwd=tmp_path)
    task = None
    try:
        await handler.start()
        assert (await handler.deliver(delivery())).status=='outcome-unknown'
        task = asyncio.create_task(handler.resume())
        async with asyncio.timeout(5):
            while len([r for r in rows(record) if r['kind']=='initialize'])<2:
                await asyncio.sleep(0.01)
        await handler.close()
        assert task.done(), 'closing a handler must settle its reconnect producer, not abandon a child process'
    finally:
        gate.touch()
        if task is not None:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await handler.close()


@pytest.mark.parametrize('state', ['pending', 'claimed'])
async def test_v2_inbox_states_are_valid_durable_receipts(tmp_path, state):
    from xdog.eyes.handlers.acp import AcpHandler

    handler = AcpHandler(enqueue_definition(tmp_path / 'peer.jsonl', mode='inbox-state-' + state), cwd=tmp_path)
    try:
        await handler.start()
        result = await handler.deliver(delivery())
        assert result.status == 'queued' and result.message_state == state
    finally:
        await handler.close()
