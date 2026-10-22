"""Flow is a host-neutral application capability with a CLI-only tool."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent.capabilities import CapabilitySet, discover
from xdog.flow import cli_process
from xdog.flow.capability import provider


async def invoke(binding, arguments, **fields):
    return json.loads(await binding.tools[0].execute('call', {'arguments': arguments, **fields}))


def patch_cli(monkeypatch, code):
    monkeypatch.setattr(cli_process, 'command', lambda arguments: (sys.executable, '-c', code, *arguments))


def workflow(path):
    path.write_text(json.dumps({'name': '中文', 'nodes': [{
        'id': 'echo', 'type': 'script', 'code': 'def echo(ctx):\n    return "你好"', 'outputs': ['result']}],
        'edges': [{'from': 'echo', 'to': '$output', 'map': {'result': 'result'}}]}, ensure_ascii=False))


async def test_application_discovery_and_registration_are_inert(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Registration must not start a process')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    class NoHost:
        def __getattr__(self, name):
            raise AssertionError('Flow must not inspect host context: ' + name)
    assert provider in discover('application')
    assert provider not in discover('session') and provider not in discover('group')
    binding = await provider.open(NoHost())
    assert getattr(provider, 'default_skills', ()) == ()
    assert provider.requirements == ()
    assert [tool.name for tool in binding.tools] == ['flow']
    assert not binding._calls
    await binding.aclose()
    await binding.aclose()
    with pytest.raises(RuntimeError, match='closed'):
        await invoke(binding, ['--help'])


async def test_real_cli_validate_run_and_failed_exit(tmp_path):
    path = tmp_path / 'workflow.json'
    workflow(path)
    binding = await provider.open(object())
    try:
        valid = await invoke(binding, ['validate', str(path), '--json'])
        assert valid['exit_code'] == 0 and json.loads(valid['stdout'])['ok']
        ran = await invoke(binding, ['run', path.name], cwd=str(tmp_path))
        assert ran['exit_code'] == 0 and '你好' in ran['stdout']
        missing = await invoke(binding, ['validate', str(tmp_path / 'missing.json')])
        assert missing['exit_code'] != 0
        assert missing['stdout'] or missing['stderr']
    finally:
        await binding.aclose()


async def test_cli_argv_is_literal_and_returns_stderr_and_nonzero_without_retry(monkeypatch, tmp_path):
    patch_cli(monkeypatch, 'import json,sys; print(json.dumps(sys.argv[1:])); '
              'sys.stderr.write("diagnostic 中文"); sys.exit(7)')
    injection = '; touch ' + str(tmp_path / 'never-created')
    binding = await provider.open(object())
    try:
        result = await invoke(binding, ['validate', injection])
        assert result['exit_code'] == 7 and json.loads(result['stdout']) == ['validate', injection]
        assert result['stderr'] == 'diagnostic 中文'
        assert not (tmp_path / 'never-created').exists()
        assert cli_process.command(('literal',))[-1] == 'literal'
    finally:
        await binding.aclose()


@pytest.mark.parametrize('params', [{}, {'arguments': []}, {'arguments': 'run x'},
    {'arguments': [None]}, {'arguments': ['run', '\0']}, {'arguments': ['run'] * 129},
    {'arguments': ['x' * 65537]}, {'arguments': ['build', 'x']},
    {'arguments': ['--help'], 'cwd': '.'}, {'arguments': ['--help'], 'cwd': None},
    {'arguments': ['--help'], 'workspace': '/tmp'}])
async def test_invalid_arguments_do_not_start_a_process(params, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Invalid arguments must not create a process')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    binding = await provider.open(object())
    try:
        with pytest.raises(ValueError):
            await binding.tools[0].execute('invalid', params)
    finally:
        await binding.aclose()


async def capture_processes(monkeypatch):
    original = asyncio.create_subprocess_exec
    processes = []
    async def spawn(*args, **kwargs):
        assert kwargs['start_new_session'] is True and kwargs['stdin'] == asyncio.subprocess.DEVNULL
        process = await original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    return processes


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('which', ['stdout', 'stderr'])
async def test_output_overflow_stops_owned_call_and_is_not_truncated_success(monkeypatch, which):
    patch_cli(monkeypatch, f'import sys,time; sys.{which}.write("x"*{cli_process._MAX_OUTPUT + 1}); '
              f'sys.{which}.flush(); time.sleep(60)')
    processes = await capture_processes(monkeypatch)
    binding = await provider.open(object())
    try:
        with pytest.raises(ValueError, match='output exceeds'):
            await invoke(binding, ['test'])
        assert len(processes) == 1 and processes[0].returncode is not None
        assert not binding._calls
    finally:
        await binding.aclose()


@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_cancel_and_close_join_calls_without_affecting_another_binding(monkeypatch, mode):
    patch_cli(monkeypatch, 'import time; time.sleep(60)')
    processes = await capture_processes(monkeypatch)
    binding, other = await provider.open(object()), await provider.open(object())
    cancel = asyncio.Event()
    task = asyncio.create_task(binding.tools[0].execute('long', {'arguments': ['run']}, cancel=cancel))
    peer = asyncio.create_task(invoke(other, ['run']))
    try:
        await until(lambda: len(processes) == 2)
        if mode == 'event':
            cancel.set()
        elif mode == 'task':
            task.cancel()
        else:
            await binding.aclose()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert processes[0].returncode is not None
        assert processes[1].returncode is None and not other.closed
        assert not binding._calls and not binding._waiters
    finally:
        peer.cancel()
        await asyncio.gather(task, peer, return_exceptions=True)
        await binding.aclose()
        await other.aclose()


async def test_cancellation_during_os_spawn_settles_and_closes_original_process(monkeypatch):
    patch_cli(monkeypatch, 'import time; time.sleep(60)')
    entered, release = asyncio.Event(), asyncio.Event()
    processes = []
    original = asyncio.create_subprocess_exec
    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    binding = await provider.open(object())
    task = asyncio.create_task(invoke(binding, ['run']))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(processes) == 1 and processes[0].returncode is not None
    await binding.aclose()


async def test_actual_tool_identity_has_conservative_execution_effect(monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: (provider,))
    capabilities = CapabilitySet('application')
    try:
        await capabilities.start(object())
        tool = next(tool for tool in capabilities.tools if tool.name == 'flow')
        effect = capabilities.tool_effect(tool, {'arguments': ['validate', '/tmp/workflow.json']})
        assert effect is not None and (effect.access, effect.scope) == ('execute', 'unknown')
    finally:
        await capabilities.aclose()


async def test_cancel_kills_workflow_descendant_that_ignores_term(monkeypatch, tmp_path):
    pid_file = tmp_path / 'descendant.pid'
    child = ('import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
             f'open({str(pid_file)!r},"w").write(str(os.getpid())); time.sleep(60)')
    patch_cli(monkeypatch, f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); '
              'time.sleep(60)')
    binding = await provider.open(object())
    task = asyncio.create_task(invoke(binding, ['run']))
    try:
        await until(lambda: pid_file.exists() and pid_file.read_text())
        pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async def stopped():
            async with asyncio.timeout(5):
                while True:
                    try:
                        state = Path(f'/proc/{pid}/stat').read_text().split()[2]
                    except FileNotFoundError:
                        return
                    if state == 'Z':  # Dead orphan awaiting the OS reaper, not an executing process.
                        return
                    await asyncio.sleep(.01)
        await stopped()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await binding.aclose()


async def test_cancellation_does_not_swallow_actual_cleanup_failure(monkeypatch):
    patch_cli(monkeypatch, 'import time; time.sleep(60)')
    processes = await capture_processes(monkeypatch)
    original = cli_process._finish
    async def broken(process, tasks):
        await original(process, tasks)
        raise RuntimeError('cleanup-failed')
    monkeypatch.setattr(cli_process, '_finish', broken)
    binding = await provider.open(object())
    task = asyncio.create_task(invoke(binding, ['run']))
    await until(lambda: processes)
    task.cancel()
    with pytest.raises(RuntimeError, match='cleanup-failed'):
        await task
    assert processes[0].returncode is not None
    await binding.aclose()


@pytest.mark.parametrize('arguments', [['--help'], ['validate', '--help'], ['run', '--help'],
    ['generate', '--help'], ['graph', '--help'], ['test', '--help'], ['scheduling', '--help']])
async def test_cli_surface_is_forwarded_without_an_embedded_api(arguments):
    binding = await provider.open(object())
    try:
        result = await invoke(binding, arguments)
        assert result['exit_code'] == 0
        assert 'usage:' in result['stdout']
    finally:
        await binding.aclose()


def test_same_environment_module_command_is_fixed():
    assert cli_process.command(('validate', 'workflow.json')) == (
        sys.executable, '-m', 'xdog.flow', 'validate', 'workflow.json')


async def test_packaged_skill_is_discoverable_but_only_explicitly_activated(tmp_path, monkeypatch):
    from xdog.agent import AgentConfig
    from xdog.agent.capabilities import CapabilityAssembly
    from xdog.agent.skills import SkillManager
    from xdog.ai.types import AssistantMessage, system_prompt_text
    from xdog.ai.utils.event_stream import EventStream

    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: (provider,) if scope == 'application' else ())
    capabilities = CapabilitySet('application')
    agents = []
    try:
        await capabilities.start(object())
        assembly = CapabilityAssembly(capabilities)
        manager = SkillManager(tmp_path / 'skills')
        assert 'flow' in {skill.slug for skill in manager.list_skills()}
        def stream(*_):
            return EventStream.empty(AssistantMessage())
        for active in ((), ('flow',)):
            agent = assembly.create_agent(stream, skills=manager, config=AgentConfig(model='offline'),
                                          active_skills=active)
            agents.append(agent)
            assert [tool.name for tool in agent.state.tools] == ['flow']
            assert agent.active_skills == active
            prompt = system_prompt_text(agent.state.system_prompt) or ''
            assert 'flow' in prompt  # The index remains available in both cases.
            assert ('# flow — author & run workflows' in prompt) is bool(active)
    finally:
        for agent in agents:
            await agent.aclose()
        await capabilities.aclose()
