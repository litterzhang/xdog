"""Protocol-neutral offline extension session fixtures for retained native behavior."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def session(tmp_path, *, extra=()):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/dialogue', root / 'dialogue')
    for name in extra:
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / name, root / name)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['dialogue', *extra], 'extension_dir': str(root)}))
    manager = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    owner = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model'),
                                   skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared")),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_repo=None, bash=None,
        permissions=PermissionManager('deny'))
    return owner, calls


async def close(owner):
    await owner.children.runtime.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def history(owner):
    """Seed persisted history for offline native compaction admission tests."""
    messages = []
    for index in range(6):
        messages.extend([UserMessage(content=f'original turn {index} ' + 'x' * 2000),
                         AssistantMessage(content=(TextContent(text=f'original answer {index}'),))])
    owner.agent.replace_messages(messages)
    owner._persist()
    return messages
