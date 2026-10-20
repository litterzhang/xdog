"""Characterize cancellation-first delivery separately from error-first ownership."""
from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest
from xdog.agent.async_work import run_owned
from xdog.agent.supervision_transfer import settled_image_work


@pytest.mark.parametrize('run', [run_owned, settled_image_work])
@pytest.mark.parametrize('fails', [False, True])
async def test_thread_delivery_retains_ownership_through_repeated_cancel(run, fails):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, error: errors.append(error))
    def work():
        entered.set()
        try:
            assert release.wait(5)
            if fails:
                raise ValueError('late thread failure must not replace cancellation')
            return 'not delivered'
        finally:
            exited.set()
    caller = asyncio.create_task(run(work))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
        assert not caller.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert exited.is_set() and not errors
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
        loop.set_exception_handler(previous)


@pytest.mark.parametrize('run', [run_owned, settled_image_work])
async def test_thread_result_failure_and_context_are_preserved(run):
    marker = contextvars.ContextVar('owned-work-test', default='outside')
    marker.set('caller context')
    token = object()
    assert await run(lambda: (marker.get(), token)) == ('caller context', token)
    failure = ValueError('same exception')
    def fail():
        raise failure
    with pytest.raises(ValueError) as raised:
        await run(fail)
    assert raised.value is failure


@pytest.mark.parametrize('fails', [False, True])
async def test_wait_owned_shields_shared_task_but_preserves_error_first_contract(fails):
    from xdog.agent.async_work import wait_owned
    entered, release = asyncio.Event(), asyncio.Event()
    failure = ValueError('owned outcome')
    async def work():
        entered.set()
        await release.wait()
        if fails:
            raise failure
        return 'result for uncancelled waiter'
    owned = asyncio.create_task(work())
    first, second = (asyncio.create_task(wait_owned(owned)) for _ in range(2))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        for _ in range(3):
            first.cancel()
            await asyncio.sleep(0)
        assert not owned.done() and owned.cancelling() == 0
        assert not first.done() and not second.done()
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        if fails:
            assert outcomes == [failure, failure]
        else:
            assert isinstance(outcomes[0], asyncio.CancelledError)
            assert outcomes[1] == 'result for uncancelled waiter'
    finally:
        release.set()
        await asyncio.gather(owned, first, second, return_exceptions=True)


async def test_wait_owned_future_already_done_and_inner_cancel_are_preserved():
    from xdog.agent.async_work import wait_owned
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    done.set_result({'done': True})
    assert await wait_owned(done) == {'done': True}
    failure = ValueError('completed failure')
    failed = loop.create_future()
    failed.set_exception(failure)
    with pytest.raises(ValueError) as raised:
        await wait_owned(failed)
    assert raised.value is failure
    cancelled = loop.create_future()
    pending = asyncio.create_task(wait_owned(cancelled))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 3)
    with pytest.raises(asyncio.CancelledError):
        await wait_owned(cancelled)


async def test_typed_thread_arguments_and_legacy_image_import_contract():
    assert await run_owned(lambda first, *, suffix: first + suffix, 'typed', suffix=' result') == 'typed result'
    assert await settled_image_work(work=lambda: 42) == 42
    import inspect
    assert settled_image_work.__name__ == 'settled_image_work'
    assert tuple(inspect.signature(settled_image_work).parameters) == ('work',)


def test_owned_work_import_has_no_extension_image_or_acp_runtime_dependency():
    import subprocess
    import sys
    code = '''
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == 'acp' or name.startswith(('acp.', 'xdog.agent.extensions', 'xdog.agent.supervision_transfer')):
            raise AssertionError('Unexpected feature dependency: ' + name)
sys.meta_path.insert(0, Guard())
from xdog.agent.async_work import run_owned, wait_owned
assert callable(run_owned) and callable(wait_owned)
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_cleanup_steps_join_all_resources_and_preserve_actual_failures_after_cancel():
    from xdog.agent import async_work

    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first_error, last_error = OSError('first actual close failed'), ValueError('last actual close failed')
    order = []
    async def first():
        order.append('first')
        raise first_error
    async def middle():
        entered.set()
        await release.wait()
        finished.set()
        order.append('middle')
    async def last():
        order.append('last')
        raise last_error
    caller = None
    try:
        caller = asyncio.create_task(async_work.settle_cleanup(first, middle, last, message='owned cleanup'))
        await asyncio.wait_for(entered.wait(), 3)
        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
        assert not caller.done() and not finished.is_set()
        release.set()
        with pytest.raises(BaseExceptionGroup) as error:
            await caller
        assert error.value.exceptions == (first_error, last_error)
        assert order == ['first', 'middle', 'last'] and finished.is_set()
    finally:
        release.set()
        if caller is not None:
            await asyncio.gather(caller, return_exceptions=True)
