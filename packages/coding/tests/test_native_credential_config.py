from __future__ import annotations

import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.mcp.oauth import OAuthError
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


def session(tmp_path, backend):
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_credential_backend': backend}))
    project = tmp_path / '.coding'
    project.mkdir(exist_ok=True)
    (project / 'settings.json').write_text(json.dumps({'mcp_credential_backend': 'file'}))
    manager = SessionManager(tmp_path / 'sessions')
    return AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage()),
                                    config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(model='offline/model', working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=settings, project_dir=tmp_path), working_dir=tmp_path,
        tool_registry=None, bash=None)


async def test_global_native_selection_survives_project_override_and_real_session_staged_restore(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    owner = session(tmp_path, 'secret-service')
    try:
        assert isinstance(owner.mcp.credential_store, NativeCredentialStore)
        old_namespace = owner.mcp.credential_store.namespace
        old_owner = owner.session_id
        owner.new_session()
        assert owner.session_id != old_owner
        assert isinstance(owner.mcp.credential_store, NativeCredentialStore)
        assert owner.mcp.credential_store.namespace == old_namespace
        assert not owner.mcp.credential_store.directory.exists()
    finally:
        await owner.children.runtime.aclose()
        await owner.extensions.aclose()
        owner.dispose()


@pytest.mark.parametrize('value', ['invalid', True, None, 1, []])
def test_invalid_global_native_choice_is_not_discarded_into_file_default(tmp_path, value):
    with pytest.raises(OAuthError, match='no fallback'):
        session(tmp_path, value)
