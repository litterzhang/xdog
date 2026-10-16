"""Confirmed independent-review defects: public adapters must respect final owner state."""
import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.batch_helpers import single_input_execute


def answer(session,text='NEW ANSWER'):
    session.agent.set_stream_fn(lambda *_:EventStream.empty(AssistantMessage(content=(TextContent(text=text),))))


def fail_terminal(monkeypatch):
    from xdog.coding.core.sessions.store import InputStore

    original=InputStore._commit

    def fail(self,records):
        if any(row.status=='succeeded' for row in records):
            raise OSError('terminal fsync failed')
        return original(self,records)

    monkeypatch.setattr(InputStore,'_commit',fail)


async def test_sdk_never_returns_success_when_terminal_persistence_failed(coding_session,monkeypatch):
    answer(coding_session)
    fail_terminal(monkeypatch)
    with pytest.raises((OSError,RuntimeError),match='terminal fsync failed'):
        await coding_session.send_message('work')


async def test_print_returns_nonzero_and_does_not_print_a_false_final_answer(coding_session,monkeypatch,capsys):
    from xdog.coding.modes.print_mode import run_print_mode

    answer(coding_session)
    fail_terminal(monkeypatch)
    code=await run_print_mode(coding_session,'work')
    output=capsys.readouterr()
    assert code!=0 and 'terminal fsync failed' in output.err
    assert 'NEW ANSWER' not in output.out


async def test_attach_answer_excludes_load_history_user_and_thought_chunks(coding_session,monkeypatch):
    from xdog.coding.acp.client import send_prompt
    from xdog.coding.core.sessions.runtime import SessionRuntime

    coding_session.agent.replace_messages([UserMessage(content='OLD QUESTION'),
                                           AssistantMessage(content=(TextContent(text='OLD ANSWER'),))])
    from xdog.ai.types import DoneEvent, StartEvent, TextDeltaEvent, ThinkingContent, ThinkingDeltaEvent

    def stream(*args):
        message=AssistantMessage(content=(ThinkingContent(thinking='PRIVATE THOUGHT'),TextContent(text='NEW ANSWER')))
        future=asyncio.get_running_loop().create_future()
        future.set_result(message)

        async def events():
            yield StartEvent(partial=message)
            yield ThinkingDeltaEvent(delta='PRIVATE THOUGHT',partial=message)
            yield TextDeltaEvent(delta='NEW ANSWER',partial=message)
            yield DoneEvent(message=message)

        return EventStream.from_async_generator(events(),result_future=future)

    coding_session.agent.set_stream_fn(stream)
    runtime=SessionRuntime(coding_session,bound=False)
    runtime.start()
    await runtime.open_endpoint()
    monkeypatch.setattr('xdog.coding.config.get_sessions_dir',lambda:coding_session.session_manager._dir)
    try:
        result=await send_prompt(coding_session.session_id,'new question')
        assert result.response.content==(TextContent(text='NEW ANSWER'),)
    finally:
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('level',['minimal','low','medium','high','xhigh','off'])
async def test_sdk_thinking_levels_all_use_the_owner(coding_session,level):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime=SessionRuntime(coding_session,bound=False)
    runtime.start(setup_pending=True)
    owned=[]
    async def configure():
        owned.append(runtime.controller.owns_current_task)
        coding_session.set_thinking_level(level)
    try:
        await asyncio.wrap_future(runtime.controller.control(configure,name='set_thinking'))
        assert owned == [True]
        assert coding_session.agent.options.thinking == level
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_sdk_failed_status_without_exception_cause_is_not_success(coding_session):
    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime=SessionRuntime(coding_session,bound=False)
    runtime.start()

    async def failed(value):
        return TurnResult('failed',AssistantMessage(content=(TextContent(text='partial'),)),'reported failure')

    runtime.controller.max_messages_per_turn = 1
    runtime.controller._execute_batch = single_input_execute(failed)
    try:
        with pytest.raises(RuntimeError,match='reported failure'):
            await coding_session.send_message('work')
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_sdk_only_navigation_cannot_mutate_branch_while_admitted_turn_is_preparing(coding_session,monkeypatch):
    target=coding_session.create_branch(label='saved')
    original_branch=coding_session.session_data.active_branch
    entered,release=asyncio.Event(),asyncio.Event()
    original=coding_session.initialize_extensions
    executed=[]

    async def initialize():
        entered.set()
        await release.wait()
        await original()

    def model(*args):
        executed.append(coding_session.session_data.active_branch)
        return EventStream.empty(AssistantMessage())

    monkeypatch.setattr(coding_session,'initialize_extensions',initialize)
    coding_session.agent.set_stream_fn(model)
    first=asyncio.create_task(coding_session.send_message('first',input_origin='system'))
    second=None
    try:
        await asyncio.wait_for(entered.wait(),3)
        controller=coding_session._sdk_controller
        second=asyncio.create_task(coding_session.send_message('second',input_origin='system'))
        async with asyncio.timeout(3):
            while len(controller.store.records())<2:
                await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError,match='owner|control'):
            coding_session.restore_branch(target)
        release.set()
        await asyncio.gather(first,second)
        assert executed==[original_branch,original_branch]
        assert all(r.branch_id==original_branch for r in controller.store.records())
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first,second) if t is not None),return_exceptions=True)


async def test_sdk_only_threaded_navigation_fences_old_queued_input(coding_session,monkeypatch):
    target=coding_session.create_branch(label='saved')
    before=coding_session.session_data.active_branch
    entered,release=asyncio.Event(),asyncio.Event()
    original=coding_session.initialize_extensions
    executed=[]

    async def initialize():
        entered.set()
        await release.wait()
        await original()

    def model(*args):
        executed.append(coding_session.session_data.active_branch)
        return EventStream.empty(AssistantMessage())

    monkeypatch.setattr(coding_session,'initialize_extensions',initialize)
    monkeypatch.setattr(coding_session,'_resolve_selected_model',lambda _:None)
    coding_session.agent.set_stream_fn(model)
    first=asyncio.create_task(coding_session.send_message('first',input_origin='system'))
    second=navigation=None
    try:
        await asyncio.wait_for(entered.wait(),3)
        controller=coding_session._sdk_controller
        second=asyncio.create_task(coding_session.send_message('second',input_origin='system'))
        async with asyncio.timeout(3):
            while len(controller.store.records())<2:
                await asyncio.sleep(0.01)
        navigation=asyncio.create_task(asyncio.to_thread(coding_session.restore_branch,target))
        await asyncio.sleep(0.03)
        assert not navigation.done(), 'SDK control must wait for its already admitted producer'
        release.set()
        results=await asyncio.gather(first,second,navigation,return_exceptions=True)
        assert results[2] is True,results
        assert executed==[before]
        assert controller.store.records()[1].status=='discarded'
        assert coding_session.session_data.active_branch==target
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first,second,navigation) if t is not None),return_exceptions=True)


def seed_compaction(session):
    session.agent.replace_messages([UserMessage(content='old '*10000),
        AssistantMessage(content=(TextContent(text='answer '*10000),)),
        UserMessage(content='recent question'),AssistantMessage(content=(TextContent(text='recent answer'),))])
    session._persist()


async def test_public_compact_waits_for_owner_preparation_reservation(coding_session,monkeypatch):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime=SessionRuntime(coding_session,bound=False)
    runtime.start()
    entered,release=asyncio.Event(),asyncio.Event()
    compacted=[]

    async def prepare(value):
        entered.set()
        await release.wait()

    async def compact_owned(**kwargs):
        compacted.append(runtime.controller.owns_current_task)

    runtime.controller.before_batch=prepare
    monkeypatch.setattr(coding_session,'_run_compaction',compact_owned)
    ticket=runtime.controller.submit(SessionInput('turn','human','Human','work',(),{}),peer_id='human')
    task=None
    try:
        await asyncio.wait_for(entered.wait(),3)
        task=asyncio.create_task(coding_session.compact())
        await asyncio.sleep(0.03)
        assert not compacted and not task.done()
        release.set()
        await asyncio.wrap_future(ticket.result)
        await task
        assert compacted==[True]
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task,return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('owner_mode',['runtime','sdk'])
async def test_public_compact_cannot_write_after_owner_closes_and_releases_lease(coding_session,owner_mode):
    from xdog.ai.types import DoneEvent
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory
    from xdog.coding.core.sessions.runtime import SessionRuntime

    seed_compaction(coding_session)
    original=coding_session.session_manager._session_path(coding_session.session_id).read_bytes()
    entered,release,cleaning,settle=asyncio.Event(),asyncio.Event(),asyncio.Event(),asyncio.Event()

    def stream(*args):
        future=asyncio.get_running_loop().create_future()

        async def events():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cleaning.set()
                await settle.wait()
                raise
            message=AssistantMessage(content=(TextContent(text='summary'),))
            future.set_result(message)
            yield DoneEvent(message=message)

        return EventStream.from_async_generator(events(),result_future=future)

    coding_session.agent.set_stream_fn(stream)
    runtime=SessionRuntime(coding_session,bound=False) if owner_mode=='runtime' else None
    if runtime is not None:
        runtime.start()
    compact=asyncio.create_task(coding_session.compact())
    closing=None
    try:
        await asyncio.wait_for(entered.wait(),3)
        if runtime is not None:
            closing=runtime.close()
        else:
            controller=coding_session._sdk_controller
            coding_session.dispose()
            closing=controller.close()
        await asyncio.sleep(0.03)
        assert not closing.done(), 'close must cancel and settle the compaction writer before releasing its lease'
        with pytest.raises(RuntimeError,match='owned'):
            other=SessionLease.acquire(runtime_directory(coding_session.session_manager._dir),coding_session.session_id)
            other.close()
        assert cleaning.is_set()
        settle.set()
        await asyncio.wait_for(asyncio.wrap_future(closing),3)
        release.set()
        await asyncio.gather(compact,return_exceptions=True)
        assert coding_session.session_manager._session_path(coding_session.session_id).read_bytes()==original
        other=SessionLease.acquire(runtime_directory(coding_session.session_manager._dir),coding_session.session_id)
        other.close()
    finally:
        release.set()
        settle.set()
        await asyncio.gather(compact,return_exceptions=True)
        if runtime is not None:
            await asyncio.gather(asyncio.wrap_future(runtime.close()),return_exceptions=True)


async def test_enabling_runtime_cannot_replace_an_active_sdk_owner(coding_session,monkeypatch):
    entered,release=asyncio.Event(),asyncio.Event()
    original=coding_session.initialize_extensions

    async def initialize():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(coding_session,'initialize_extensions',initialize)
    task=asyncio.create_task(coding_session.send_message('work'))
    try:
        await asyncio.wait_for(entered.wait(),3)
        current=coding_session._sdk_controller
        with pytest.raises(RuntimeError,match='SDK owner|second.*owner'):
            coding_session.enable_runtime(bound=True)
        assert coding_session._sdk_controller is current and coding_session.runtime is None
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)
