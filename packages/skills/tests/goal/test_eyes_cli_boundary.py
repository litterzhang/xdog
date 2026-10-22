"""CLI-only Goal boundary: fixed argv, bounded JSON, and owned process cleanup."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from xdog.skills.goal import eyes_cli


def program(monkeypatch, source):
    monkeypatch.setattr(eyes_cli, '_command', lambda owner, arguments: (sys.executable, '-c', source))


def test_fixed_module_entrypoint_and_no_shell_interpolation(tmp_path):
    dangerous = '; touch /tmp/not-a-command'
    assert eyes_cli._command('goal:original', ('status', dangerous)) == (
        sys.executable, '-m', 'xdog.eyes', '--owner', 'goal:original',
        'status', dangerous)


@pytest.mark.parametrize('payload', ['[]', 'not json', '{"value":NaN}', '{"value":1e9999}'])
async def test_invalid_nonfinite_or_nonobject_response_is_rejected(tmp_path, monkeypatch, payload):
    program(monkeypatch, f'print({payload!r})')
    with pytest.raises(eyes_cli.EyesCLIError, match='JSON'):
        await eyes_cli.invoke('test', 'status')


async def test_cli_error_is_not_retried_or_interpreted_as_another_command(tmp_path, monkeypatch):
    program(monkeypatch, 'import sys; sys.stderr.write("original operation refused"); sys.exit(2)')
    with pytest.raises(eyes_cli.EyesCLIError, match='original operation refused'):
        await eyes_cli.invoke('test', 'start')


async def test_json_input_is_stdin_not_argv_or_shell(tmp_path, monkeypatch):
    program(monkeypatch, 'import json,sys; value=json.load(sys.stdin); print(json.dumps({"seen":value}))')
    data = {'text': '中文 " ; $(never_execute)'}
    assert await eyes_cli.invoke('test', 'start', '-', payload=data) == {'seen': data}


async def capture_processes(monkeypatch):
    original = asyncio.create_subprocess_exec
    created = []

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    return created


async def test_timeout_joins_only_owned_cli_process(tmp_path, monkeypatch):
    program(monkeypatch, 'import time; time.sleep(60)')
    created = await capture_processes(monkeypatch)
    with pytest.raises(TimeoutError):
        await eyes_cli.invoke('test', 'status', timeout=.1)
    assert len(created) == 1 and created[0].returncode is not None


async def test_cancellation_during_spawn_settles_then_closes_actual_process(tmp_path, monkeypatch):
    program(monkeypatch, 'import time; time.sleep(60)')
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.create_subprocess_exec
    created = []

    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        entered.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    invoking = asyncio.create_task(eyes_cli.invoke('test', 'status'))
    await asyncio.wait_for(entered.wait(), 3)
    invoking.cancel()
    await asyncio.sleep(.02)
    assert not invoking.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await invoking
    assert created[0].returncode is not None


async def test_oversized_stdout_is_bounded_and_process_is_closed(tmp_path, monkeypatch):
    program(monkeypatch, f'import sys,time; sys.stdout.write("x"*{eyes_cli._MAX_OUTPUT + 1});'
                         'sys.stdout.flush(); time.sleep(60)')
    created = await capture_processes(monkeypatch)
    with pytest.raises(eyes_cli.EyesCLIError, match='capacity'):
        await eyes_cli.invoke('test', 'status')
    assert created[0].returncode is not None


async def test_follow_detach_closes_its_cli_not_an_observer_or_daemon(tmp_path, monkeypatch):
    program(monkeypatch, 'import time; print("{\\"sequence\\":1}", flush=True); time.sleep(60)')
    created = await capture_processes(monkeypatch)
    stream = eyes_cli.events('test')
    assert await anext(stream) == {'sequence': 1}
    await stream.aclose()
    assert len(created) == 1 and created[0].returncode is not None


async def test_oversized_stderr_cannot_strand_idle_event_stdout(tmp_path, monkeypatch):
    program(monkeypatch, f'import sys,time; sys.stderr.write("x"*{eyes_cli._MAX_ERROR + 1});'
                         'sys.stderr.flush(); time.sleep(60)')
    created = await capture_processes(monkeypatch)
    stream = eyes_cli.events('test')
    with pytest.raises(eyes_cli.EyesCLIError, match='capacity'):
        async with asyncio.timeout(3):
            await anext(stream)
    assert created[0].returncode is not None


def test_goal_source_has_no_eyes_sdk_protocol_or_runtime_imports():
    import ast

    import xdog.skills.goal.automation as automation
    for path in (Path(automation.__file__), Path(eyes_cli.__file__)):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(item.name == 'xdog.eyes' or item.name.startswith('xdog.eyes.') for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith('xdog.eyes')


async def test_follow_stdout_eof_does_not_wait_forever_for_cli_exit(tmp_path, monkeypatch):
    program(monkeypatch, 'import os,time; os.close(1); time.sleep(60)')
    created = await capture_processes(monkeypatch)
    stream = eyes_cli.events('test')
    with pytest.raises(eyes_cli.EyesCLIError, match='stream ended'):
        async with asyncio.timeout(3):
            await anext(stream)
    assert created[0].returncode is not None


async def test_capacity_failure_collects_process_wait_tasks_too(tmp_path, monkeypatch):
    program(monkeypatch, f'import sys,time; sys.stdout.write("x"*{eyes_cli._MAX_OUTPUT + 1});'
                         'sys.stdout.flush(); time.sleep(60)')
    before = set(asyncio.all_tasks())
    with pytest.raises(eyes_cli.EyesCLIError, match='capacity'):
        await eyes_cli.invoke('test', 'status')
    leaked = [task for task in asyncio.all_tasks() - before
              if not task.done() and 'Process.wait' in task.get_coro().__qualname__]
    assert leaked == []
