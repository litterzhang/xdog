"""Project evidence and one shared skill index reach the actual model Context."""
from __future__ import annotations

import asyncio
import threading

from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, Model
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core import resource_loader, slash_commands
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

from packages.coding.tests.test_sdk_session_state import _Provider


async def test_real_sdk_model_receives_supported_instructions_and_single_skill_index(tmp_path, monkeypatch):
    (tmp_path / 'AGENTS.md').write_text('PROJECT_AGENT_MARKER')
    project = tmp_path / '.coding'
    project.mkdir()
    (project / 'INSTRUCTIONS.md').write_text('PROJECT_UPPER_MARKER')
    (project / 'instructions.md').write_text('PROJECT_LOWER_MARKER')
    (tmp_path / 'CLAUDE.md').write_text('OTHER_HARNESS_CONFIG_MARKER')
    shared = tmp_path / 'skills'
    skill = shared / 'sample'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: sample\ndescription: UNIQUE_SKILL_INDEX\n---\nSKILL_BODY')
    manager = SkillManager(shared_dir=shared, packaged={})
    monkeypatch.setattr(slash_commands, 'skill_manager', lambda: manager)
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    provider = _Provider(Model(id='offline/model', context_window=100_000))
    contexts = []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage())
    monkeypatch.setattr(provider, 'stream', stream)
    monkeypatch.setattr('xdog.ai.load', lambda: provider)
    session = await asyncio.to_thread(create_agent_session, CreateSessionOptions(working_dir=tmp_path))
    session = session.session
    try:
        await asyncio.to_thread(session.activate_skill, 'sample')
        await session.send_message('show project instructions')
        prompt = contexts[-1].system_prompt
        assert 'OTHER_HARNESS_CONFIG_MARKER' not in prompt
        for marker in ('PROJECT_AGENT_MARKER', 'PROJECT_UPPER_MARKER', 'PROJECT_LOWER_MARKER',
                       'UNIQUE_SKILL_INDEX', 'SKILL_BODY'):
            assert prompt.count(marker) == 1, (marker, prompt)
        (tmp_path / 'AGENTS.md').write_text('UPDATED_PROJECT_MARKER')
        await session.send_message('second turn')
        assert 'UPDATED_PROJECT_MARKER' in contexts[-1].system_prompt
        assert 'PROJECT_AGENT_MARKER' not in contexts[-1].system_prompt
        assert session.permissions.mode == 'ask'
    finally:
        await session.aclose()


async def test_project_preparation_uses_owned_worker_not_model_owner(coding_session, monkeypatch):
    runtime = coding_session.enable_runtime(bound=False)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller._async(runtime.activate_inputs))
    entered, release = threading.Event(), threading.Event()
    original = resource_loader.discover_context_files
    threads = []
    def delayed(directory):
        threads.append(threading.current_thread())
        entered.set()
        assert release.wait(6)
        return original(directory)
    monkeypatch.setattr(resource_loader, 'discover_context_files', delayed)
    turn = asyncio.create_task(coding_session.send_message('prepare slowly'))
    try:
        assert await asyncio.to_thread(entered.wait, 2), 'Actual turn did not prepare project resources'
        assert threads[0] is not runtime.controller._thread
        await asyncio.wait_for(asyncio.wrap_future(runtime.controller._async(lambda: asyncio.sleep(0))), .5)
        assert not turn.done()
        release.set()
        await turn
    finally:
        release.set()
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await coding_session.aclose()


async def test_builtin_skill_activation_prepares_off_owner_loop(coding_session, tmp_path, monkeypatch):
    manager = SkillManager(shared_dir=tmp_path / 'skills', packaged={})
    skill = tmp_path / 'skills' / 'sample'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: sample\ndescription: example\n---\nBODY')
    monkeypatch.setattr(slash_commands, 'skill_manager', lambda: manager)
    runtime = coding_session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    reads = []
    original = manager.load_skill
    def observe(slug):
        reads.append(threading.current_thread())
        return original(slug)
    monkeypatch.setattr(manager, 'load_skill', observe)
    try:
        result = await slash_commands.execute_command('sample', '', coding_session)
        assert 'Activated skill' in result.output
        assert reads and all(thread is not runtime.controller._thread for thread in reads)
    finally:
        await coding_session.aclose()
