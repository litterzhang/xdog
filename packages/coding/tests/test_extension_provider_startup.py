from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, Model
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

ALIAS = 'ext-providermock-local/echo'


class Base:
    calls = 0
    def models(self):
        return (Model(id='base/model', context_window=99999),)
    def model(self, name):
        assert not name.startswith('ext-')
        return self.models()[0] if name == 'base/model' else None
    def stream(self, *args):
        self.calls += 1
        return EventStream.empty(AssistantMessage())


@pytest.mark.parametrize('resume', [False, True])
async def test_sync_sdk_configured_or_saved_extension_model_bootstraps_once_even_inside_running_loop(
        tmp_path, monkeypatch, resume):
    root, home = tmp_path / 'extensions', tmp_path / 'data'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providermock'],
                                   'extension_dir': str(root)}))
    monkeypatch.setenv('CODING_DIR', str(home))
    base = Base()
    first = second = None
    try:
        with patch('xdog.ai.load', return_value=base):
            result = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings,
                                                               model_name=ALIAS))
            first = result.session
            assert result.model_fallback_message is None and first.model == ALIAS
            assert first.agent.context_window == first.context_window == 32000
            assert await first.extensions.command('ext-providermock-count', '') == '0'
            assert len(first.extensions._manager._clients) == 1
            if resume:
                key = first.session_id
                first.dispose()
                await first.extensions.aclose()
                second = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings,
                                                                   resume_id=key)).session
                assert second.model == ALIAS and second.model_info.id == ALIAS
                target = second
            else:
                target = first
            await target.send_message('startup provider')
            assert await target.extensions.command('ext-providermock-count', '') == '1'
            assert base.calls == 0
    finally:
        for session in (first, second):
            if session is not None:
                await session.children.supervisor.aclose()
                await session.extensions.aclose()
                await session.mcp.aclose()


async def test_untrusted_or_missing_startup_alias_never_falls_back_or_rewrites_existing_session(tmp_path, monkeypatch):
    home = tmp_path / 'data'
    home.mkdir()
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': []}))
    monkeypatch.setenv('CODING_DIR', str(home))
    base = Base()
    with patch('xdog.ai.load', return_value=base):
        with pytest.raises(ValueError, match='explicitly trusted'):
            create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings, model_name=ALIAS))
    assert base.calls == 0


async def test_saved_alias_activation_inside_existing_session_restores_its_own_runtime(tmp_path, monkeypatch):
    root, home = tmp_path / 'extensions', tmp_path / 'data'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providermock'],
                                   'extension_dir': str(root)}))
    monkeypatch.setenv('CODING_DIR', str(home))
    base = Base()
    session = None
    try:
        with patch('xdog.ai.load', return_value=base):
            session = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings,
                                                                 model_name=ALIAS)).session
            first_id = session.session_id
            session.create_branch(label='extension branch')
            await session.send_message('first alias')
            other = session.session_manager.create_session(model='base/model', working_dir=tmp_path)
            assert session.switch_session(other.session_id)
            assert session.switch_session(first_id)
            assert session.model == ALIAS and session.model_info.id == ALIAS
            assert session.agent.context_window == 32000
            await session.send_message('activated saved alias')
            assert base.calls == 0
    finally:
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            await session.mcp.aclose()
