from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import AssistantMessage, Model
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

ALIAS = 'ext-providerauth-local/echo'
SECRET = 'offline-coding-ui044-private-token'


class Base:
    def models(self):
        return (Model(id='base/model', context_window=99999),)

    def model(self, name):
        return self.models()[0] if name == 'base/model' else None

    def stream(self, *args):
        pytest.fail('Auth-bound extension alias must never invoke native fallback')
        return EventStream.empty(AssistantMessage())


@pytest.mark.parametrize('failure', [False, True])
async def test_coding_real_ui_auth_catalog_resume_persistence_and_explicit_withdrawal(tmp_path, monkeypatch, failure):
    root, home = tmp_path / 'extensions', tmp_path / 'data'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providerauth'],
        'extension_dir': str(root), 'extension_settings': {'providerauth': {'mode': 'fail' if failure else ''}}}))
    monkeypatch.setenv('CODING_DIR', str(home))
    monkeypatch.setenv('XDOG_UI044_TEST_KEY', SECRET)
    sessions = []
    ui = None
    try:
        with patch('xdog.ai.load', return_value=Base()):
            first = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings)).session
            sessions.append(first)
            assert first.model == ALIAS
            assert await first.extensions.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=0'
            first.dispose()
            await first.extensions.aclose()
            session = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings,
                                                                 resume_id=first.session_id)).session
            sessions.append(session)
        ui = InteractiveMode(session)

        async def wait(predicate):
            async with asyncio.timeout(8):
                while not predicate():
                    ui._poll()
                    await asyncio.sleep(0.003)

        assert session.model == ALIAS
        ui._handle_slash_command('model', '')
        await wait(lambda: ui._command is None)
        assert ALIAS in '\n'.join(ui._chat_log.render(140))
        assert await session.extensions.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=0'
        ui._handle_submit('explicit offline authenticated provider turn')
        await wait(lambda: not ui._worker_active and not ui._is_busy
                   and any(message.role == 'assistant' for message in session.agent.messages))
        expected = 'Extension provider credentials unavailable' if failure else 'AUTHENTICATED-OFFLINE-ANSWER'
        assert expected in '\n'.join(ui._chat_log.render(140))
        assert await session.extensions.command('ext-providerauth-count', '') == (
            f'CALLS={0 if failure else 1};RESOLUTIONS=1')
        saved = session.session_manager.load_session(session.session_id)
        assert saved.model == ALIAS and saved.messages[-1].stop_reason == ('error' if failure else 'stop')
        assert SECRET not in repr(session.extensions._infos)
        assert SECRET not in '\n'.join(ui._chat_log.render(140))
        assert SECRET not in repr(saved)
        child = await session.children.supervisor.start('independent authenticated child',
            config=ChildConfig(model=ALIAS, max_model_calls=1).to_dict())
        record = await session.children.supervisor.control('wait', child)
        assert record['status'] == ('failed' if failure else 'completed') and SECRET not in repr(record)
        assert record['effective']['model'] == ALIAS and session.model == ALIAS
        assert await session.extensions.command('ext-providerauth-count', '') == (
            f'CALLS={0 if failure else 1};RESOLUTIONS=1')
        for path in home.rglob('*.json'):
            assert SECRET not in path.read_text()
        ui._handle_slash_command('extensions', 'unload providerauth')
        await wait(lambda: ui._command is None)
        ui._handle_submit('withdrawn alias cannot trigger auth or native fallback')
        await wait(lambda: not ui._worker_active)
        assert session.model == ALIAS and not session.extensions.provider_models()
    finally:
        if ui is not None:
            ui._request_exit()
        for session in sessions:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            await session.mcp.aclose()
