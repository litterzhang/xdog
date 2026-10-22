"""Actual CLI host paths use only fake installed capabilities and offline models."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from xdog.agent import Agent
from xdog.agent._cli_host import application_agents
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream

from packages.agent.tests.test_capability_assembly import Provider


@pytest.fixture
def offline(monkeypatch, tmp_path):
    events, calls, agents = [], [], []
    provider = Provider('application', skill='guard')
    path = tmp_path / 'packaged' / 'guard'
    path.mkdir(parents=True)
    (path / 'SKILL.md').write_text('---\nname: guard\ndescription: test instructions\n---\nCLI_SKILL_BODY')
    real_open, real_close = provider.open, provider.binding.aclose
    async def opened(host):
        events.append('application-open')
        return await real_open(host)
    async def closed():
        assert all(agent.closed for agent in agents)
        events.append('application-close')
        await real_close()
    provider.open, provider.binding.aclose = opened, closed
    def discover(scope):
        assert scope == 'application'
        events.append('discover')
        return (provider,)
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', discover)
    def manager(shared, group):
        return SkillManager(shared, group, packaged={'guard': path})
    monkeypatch.setattr('xdog.agent._cli_host.SkillManager', manager)
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='offline answer'),)))
    runtime = SimpleNamespace(active_providers=lambda: ('offline',))
    monkeypatch.setattr('xdog.ai.load', lambda: runtime)
    monkeypatch.setattr('xdog.agent.helpers.stream_fn_from_provider', lambda _: stream)
    # No web-search provider implementation or real model can run.
    monkeypatch.setattr('xdog.agent.helpers.web_search_fn_from_provider', lambda *_: None)
    original = Agent.__init__
    def record(self, *args, **kwargs):
        original(self, *args, **kwargs)
        agents.append(self)
    monkeypatch.setattr(Agent, '__init__', record)
    monkeypatch.chdir(tmp_path)
    return SimpleNamespace(provider=provider, events=events, calls=calls, agents=agents, root=tmp_path)


@pytest.mark.parametrize('no_tools', [False, True])
async def test_chat_entrypoint_assembles_skills_and_application_and_closes_in_order(offline, monkeypatch, no_tools):
    from xdog.agent.cli import _cmd_chat

    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    if no_tools:
        monkeypatch.setattr('xdog.agent.helpers.web_search_fn_from_provider',
                            lambda *_: pytest.fail('No-tools cannot enable search'))
    await _cmd_chat(model_id='offline', message='hello', system_prompt=None, temperature=None,
                    max_tokens=None, thinking=None, no_tools=no_tools, verbose=False)
    agent, = offline.agents
    assert agent.closed and agent._skills is not None
    assert len(offline.calls) == 1
    if no_tools:
        assert not agent.state.tools and not agent.active_skills and not offline.events
        assert 'CLI_SKILL_BODY' not in str(offline.calls[0].system_prompt)
        assert 'guard' in str(offline.calls[0].system_prompt)  # Index still discoverable.
    else:
        assert agent.active_skills == ('guard',)
        assert 'CLI_SKILL_BODY' in str(offline.calls[0].system_prompt)
        assert 'application' in {tool.name for tool in agent.state.tools}
        assert offline.events == ['discover', 'application-open', 'application-close']


@pytest.mark.parametrize('no_tools', [False, True])
@pytest.mark.parametrize('serve_failure', [False, True])
async def test_acp_entrypoint_assembles_every_session_and_closes_on_transport_failure(
    offline, monkeypatch, no_tools, serve_failure,
):
    from xdog.agent.acp.cli import run
    from xdog.agent.acp.contracts import Peer

    async def serve(adapter):
        peer = Peer('offline-peer', None)
        for _ in range(2):
            await adapter.backend.new_session(offline.root, peer)
        assert len(offline.agents) == 2
        assert offline.provider.opens == (0 if no_tools else 1)
        if serve_failure:
            raise OSError('transport failed before adapter cleanup')
        await adapter.backend.disconnect(peer)
    monkeypatch.setattr('xdog.agent.acp.cli.serve', serve)
    if serve_failure:
        with pytest.raises(OSError, match='transport failed'):
            await run('offline', no_tools=no_tools)
    else:
        await run('offline', no_tools=no_tools)
    assert all(agent.closed for agent in offline.agents) and not offline.calls
    for agent in offline.agents:
        assert agent._skills is not None
        assert agent.active_skills == (() if no_tools else ('guard',))
        assert [tool.name for tool in agent.state.tools] == ([] if no_tools else ['application'])
    assert offline.provider.closes == (0 if no_tools else 1)


async def test_no_tools_never_discovers_capabilities(offline, monkeypatch):
    def forbidden(*args):
        pytest.fail('No-tools must not discover/open any installed application provider')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', forbidden)
    async with application_agents(lambda *_: pytest.fail('No model'), no_tools=True) as create:
        agent = await create(offline.root)
        assert not agent.state.tools and agent._skills is not None
    assert agent.closed


@pytest.mark.parametrize('failure', ['manager', 'placement', 'agent_close', 'body', 'start'])
async def test_cli_cleanup_survives_assembly_close_and_start_failures(offline, monkeypatch, failure):
    if failure == 'manager':
        def broken(*args):
            raise OSError('manager failed')
        monkeypatch.setattr('xdog.agent._cli_host.SkillManager', broken)
    elif failure == 'placement':
        (offline.root / 'packaged' / 'guard' / 'SKILL.md').unlink()
    elif failure == 'agent_close':
        original = Agent.aclose
        async def broken_close(agent):
            await original(agent)
            raise OSError('agent close failed')
        monkeypatch.setattr(Agent, 'aclose', broken_close)
    elif failure == 'start':
        async def broken_start(host):
            raise OSError('application start failed')
        offline.provider.open = broken_start
    expected = ValueError if failure == 'placement' else OSError
    with pytest.raises(expected):
        async with application_agents(lambda *_: pytest.fail('No model')) as create:
            await create(offline.root)
            if failure == 'body':
                raise OSError('host body failed')
    assert offline.provider.closes == (0 if failure == 'start' else 1)
    assert all(agent.closed for agent in offline.agents)


async def test_cleanup_closes_all_agents_before_provider_even_on_multiple_failures(offline, monkeypatch):
    original = Agent.aclose
    async def broken(agent):
        await original(agent)
        raise OSError('agent close error')
    monkeypatch.setattr(Agent, 'aclose', broken)
    with pytest.raises(BaseExceptionGroup) as caught:
        async with application_agents(lambda *_: pytest.fail('No model')) as create:
            await create(offline.root)
            await create(offline.root)
            raise ValueError('original host error')
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert isinstance(caught.value.exceptions[1], BaseExceptionGroup)
    assert offline.provider.closes == 1 and all(agent.closed for agent in offline.agents)


async def test_cancellation_settles_agents_before_application(offline):
    ready = asyncio.Event()
    async def host():
        async with application_agents(lambda *_: pytest.fail('No model')) as create:
            await create(offline.root)
            ready.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(host())
    await asyncio.wait_for(ready.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert offline.provider.closes == 1 and all(agent.closed for agent in offline.agents)


@pytest.mark.parametrize('command', ['chat', 'acp'])
def test_both_cli_parsers_forward_no_tools(monkeypatch, command):
    from xdog.agent.cli import main

    calls = []
    async def invoke(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr('sys.argv', ['xdog-agent', command, '--no-tools'])
    monkeypatch.setattr('xdog.agent.cli._cmd_chat', invoke)
    monkeypatch.setattr('xdog.agent.acp.cli.run', invoke)
    main()
    assert len(calls) == 1 and calls[0][1]['no_tools'] is True
