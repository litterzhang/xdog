from __future__ import annotations

import json
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
