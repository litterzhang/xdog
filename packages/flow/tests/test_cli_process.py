"""Real CLI subprocesses must relinquish every pipe before their loop retires."""
from __future__ import annotations

import asyncio
import gc
import os
import signal
import sys
import warnings

import pytest
from xdog.flow import cli_process


def assert_settled(process):
    assert process.returncode is not None, 'CLI cleanup did not reap its process'
    assert process._transport.is_closing(), 'CLI cleanup left its subprocess transport open'
    assert set(process._transport._pipes) == {1, 2}
    assert all(pipe.disconnected for pipe in process._transport._pipes.values()), (
        'CLI cleanup returned before every output pipe disconnected')


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.001)


async def joined(task):
    done, _ = await asyncio.wait({task}, timeout=5)
    assert done, 'CLI cleanup blocked on a backpressured pipe'
    return task.result()


async def salvage(processes, task):
    # RED-path salvage: assert settlement before this, but never leak a test child.
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process._transport.close()
    await asyncio.gather(task, return_exceptions=True)
    for process in processes:
        await process.communicate()


def run_on_retiring_loop(exercise):
    # Collect earlier tests' garbage before observing this call's resources.
    gc.collect()
    errors = []
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always', ResourceWarning)
        # An explicit factory leaves pytest's current event loop untouched.
        with asyncio.Runner(debug=True, loop_factory=asyncio.new_event_loop) as runner:
            loop = runner.get_loop()
            loop.set_exception_handler(lambda _loop, context: errors.append(context))
            runner.run(exercise())
            assert not asyncio.all_tasks(loop), 'CLI left pending work on its retiring loop'
        assert loop.is_closed()
        gc.collect()
    assert not errors, errors
    resource_warnings = [str(item.message) for item in emitted if issubclass(item.category, ResourceWarning)]
    assert not resource_warnings, resource_warnings


def capture(monkeypatch, code, *, delay=False):
    monkeypatch.setattr(cli_process, 'command', lambda _: (sys.executable, '-c', code))
    original = asyncio.create_subprocess_exec
    processes = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        if delay:
            await release.wait()
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    return processes, entered, release


@pytest.mark.parametrize('size', [0, cli_process._MAX_OUTPUT])
def test_normal_output_settles_process_and_all_pipes_before_loop_exit(monkeypatch, size):
    processes, _, _ = capture(monkeypatch,
        f'import sys; sys.stdout.buffer.write(b"x"*{size}); sys.stderr.buffer.write(b"y"*{size}); sys.exit(7)')

    async def exercise():
        task = asyncio.create_task(cli_process.run(('run',), None))
        try:
            result = await joined(task)
            assert result == {'exit_code': 7, 'stdout': 'x' * size, 'stderr': 'y' * size}
            assert len(processes) == 1
            assert_settled(processes[0])
        finally:
            await salvage(processes, task)

    run_on_retiring_loop(exercise)


@pytest.mark.parametrize('which', ['stdout', 'stderr'])
def test_overflow_settles_a_paused_pipe_before_loop_exit(monkeypatch, which):
    processes, _, _ = capture(monkeypatch,
        f'import sys,time; sys.{which}.buffer.write(b"x"*{4 * cli_process._MAX_OUTPUT}); time.sleep(60)')
    original = cli_process._stop
    paused = []

    async def stop(process):
        # The failed bounded reader no longer consumes output. Wait for actual
        # backpressure, rather than relying on a lucky race with child exit.
        await until(lambda: getattr(process, which)._paused)
        paused.append(which)
        await original(process)

    monkeypatch.setattr(cli_process, '_stop', stop)

    async def exercise():
        task = asyncio.create_task(cli_process.run(('run',), None))
        try:
            with pytest.raises(ValueError, match='output exceeds'):
                await joined(task)
            assert paused == [which]
            assert len(processes) == 1
            assert_settled(processes[0])
        finally:
            await salvage(processes, task)

    run_on_retiring_loop(exercise)


def test_repeated_spawn_cancel_settles_both_backpressured_pipes_before_loop_exit(monkeypatch):
    code = ('import os,signal,threading,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
            f'[threading.Thread(target=os.write,args=(fd,b"x"*{4 * cli_process._MAX_OUTPUT}),daemon=True).start() '
            'for fd in (1,2)]; time.sleep(60)')
    processes, entered, release = capture(monkeypatch, code, delay=True)

    async def exercise():
        task = asyncio.create_task(cli_process.run(('run',), None))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            process = processes[0]
            await until(lambda: process.stdout._paused and process.stderr._paused)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), 'Cancellation abandoned the unreturned process handle'
                assert process.returncode is None
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await joined(task)
            assert_settled(process)
        finally:
            release.set()
            await salvage(processes, task)

    run_on_retiring_loop(exercise)


def test_repeated_cancel_during_cleanup_keeps_process_on_original_loop(monkeypatch):
    processes, spawned, _ = capture(monkeypatch, 'import time; time.sleep(60)')
    entered, release = asyncio.Event(), asyncio.Event()
    original = cli_process._stop

    async def stop(process):
        entered.set()
        await release.wait()
        await original(process)

    monkeypatch.setattr(cli_process, '_stop', stop)

    async def exercise():
        task = asyncio.create_task(cli_process.run(('run',), None))
        try:
            await asyncio.wait_for(spawned.wait(), 5)
            # Let run receive the published handle and start its owned readers.
            await until(lambda: any(item.get_coro().__name__ == '_read' for item in asyncio.all_tasks()))
            task.cancel()
            await asyncio.wait_for(entered.wait(), 5)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), 'Cancellation published before process cleanup settled'
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await joined(task)
            assert_settled(processes[0])
        finally:
            release.set()
            await salvage(processes, task)

    run_on_retiring_loop(exercise)
