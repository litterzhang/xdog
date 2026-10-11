from __future__ import annotations

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

ALIAS = 'ext-providercaps-local/gpt-5.1/code:preview'


async def test_coding_model_catalog_compound_id_prompt_limit_vision_effort_and_independent_child(tmp_path, monkeypatch):
    root, home = tmp_path / 'extensions', tmp_path / 'data'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providercaps'],
                                    'extension_dir': str(root), 'thinking_level': 'low'}))
    monkeypatch.setenv('CODING_DIR', str(home))
    calls = []

    class Base:
        def models(self):
            return (Model(id='base/model', context_window=99999),)
        def model(self, name):
            assert not name.startswith('ext-')
            return self.models()[0] if name == 'base/model' else None
        def stream(self, *args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())

    with patch('xdog.ai.load', return_value=Base()):
        session = create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings,
                                      model_name=ALIAS)).session
    ui = InteractiveMode(session)
    try:
        assert session.model == ALIAS and session.context_limit == session.agent.max_prompt_tokens == 30000
        assert session.agent.context_window == 48000 and session.model_info.vision_limits.max_prompt_images == 1
        session.set_thinking_level('high')
        previous = session.agent.options
        with pytest.raises(ValueError, match='not advertised'):
            session.set_thinking_level('xhigh')
        assert session.agent.options == previous and session.settings.thinking_level == 'high'
        with pytest.raises(ValueError, match='at most 1'):
            session.validate_image_inputs((('image/png', 68), ('image/png', 68)))
        await session.send_message('metadata-routed turn')
        assert await session.extensions.command('ext-providercaps-count', '') == 'CALLS=1' and not calls
        child = await session.children.supervisor.start('native metadata child', config=ChildConfig(
            model=ALIAS, thinking='low', max_model_calls=1).to_dict())
        record = await session.children.supervisor.control('wait', child)
        assert record['status'] == 'completed' and record['effective']['max_prompt_tokens'] == 30000
        assert record['effective']['thinking'] == 'low' and record['calls'] == 1
        assert await session.extensions.command('ext-providercaps-count', '') == 'CALLS=1'
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        await session.mcp.aclose()


async def test_configured_unsupported_extension_effort_fails_startup_without_provider_fallback(tmp_path, monkeypatch):
    root, home = tmp_path / 'extensions', tmp_path / 'data'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providercaps'],
                                    'extension_dir': str(root), 'thinking_level': 'xhigh'}))
    monkeypatch.setenv('CODING_DIR', str(home))
    class Base:
        def models(self):
            return (Model(id='base/model'),)
        def model(self, name):
            raise AssertionError('Explicit extension choice must not resolve/fall back through base catalog')
        def stream(self, *args):
            raise AssertionError('No provider calls during startup validation')
    with patch('xdog.ai.load', return_value=Base()):
        with pytest.raises(ValueError, match='Configured thinking level'):
            create_agent_session(CreateSessionOptions(working_dir=tmp_path, config_path=settings, model_name=ALIAS))
